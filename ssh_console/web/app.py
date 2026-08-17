"""The HTTP application: routes mirroring the Go original one-for-one, a small
server-rendered UI, and the WebSocket terminal. No accounts, no login — a
single-user local tool bound to localhost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import (
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from .palette import COMMAND_GROUPS
from .recorder import TermRecorder
from .. import remote as remote_pkg

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"

_RECORD_CAP = 512 * 1024  # 512 KiB captured, matching the Go recorder


# --- template helpers ------------------------------------------------------

def _ago(t: datetime | None) -> str:
    if t is None:
        return "never"
    secs = (datetime.now() - t).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _fsize(n: int) -> str:
    unit = 1024
    if n < unit:
        return f"{n} B"
    div, exp, x = unit, 0, n // unit
    while x >= unit:
        div *= unit
        exp += 1
        x //= unit
    return f"{n / div:.1f} {'KMGTPE'[exp]}iB"


def _fmt_duration(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


# --- recursive-delete guard ------------------------------------------------

# Directories whose recursive deletion would destroy the machine rather than remove
# a file. A guardrail against a slip, not a security boundary. Deliberately short.
_PROTECTED_SYSTEM_PATHS = {
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/root",
    "/sbin", "/sys", "/usr", "/var", "/home", "/opt",
}


def resolve_delete_target(raw: str) -> str:
    """Apply the recursive-delete guard to a raw form value. Pure on purpose: this
    is the only thing between a mistyped form and the whole filesystem. posixpath,
    not os.path — the target is always Linux, SSH Console may not be."""
    target = posixpath.normpath((raw or "").strip())
    if target in ("", ".") or target.strip("/") == "":
        raise ValueError("refusing to delete the filesystem root")
    if target in _PROTECTED_SYSTEM_PATHS:
        raise ValueError(f"refusing to recursively delete a system directory: {target}")
    return target


def _breadcrumbs(p: str) -> list[dict]:
    out = [{"name": "/", "path": "/"}]
    cur = "/"
    for seg in p.strip("/").split("/"):
        if not seg:
            continue
        cur = posixpath.join(cur, seg)
        out.append({"name": seg, "path": cur})
    return out


def create_app(store, manager, cfg, log: logging.Logger | None = None) -> FastAPI:
    log = log or logging.getLogger("ssh_console.web")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES))
    templates.env.globals.update(ago=_ago, fsize=_fsize, fmt_duration=_fmt_duration)

    # Concurrent-terminal cap state.
    term_state = {"live": 0}
    term_lock = asyncio.Lock()

    def shell(nav: str, active_id: str) -> dict:
        return {
            "nav": nav,
            "sidebar_servers": store.list_ssh_servers(),
            "active_id": active_id,
        }

    def render(name: str, request: Request, data: dict) -> Response:
        # Starlette's current signature is (request, name, context); the context no
        # longer carries the request itself.
        return templates.TemplateResponse(request, name, data)

    def redirect(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def redirect_files(id: str, dir: str) -> RedirectResponse:
        return redirect(f"/servers/{id}/files?path={quote(dir)}")

    # --- connections -------------------------------------------------------

    @app.get("/")
    async def list_servers(request: Request):
        servers = store.list_ssh_servers()
        data = shell("servers", "")
        data["servers"] = servers
        data["error"] = request.query_params.get("err", "")
        return render("list.html", request, data)

    @app.post("/servers")
    async def add_server(request: Request):
        form = await request.form()
        name = (form.get("name") or "").strip()
        host = (form.get("host") or "").strip()
        username = (form.get("username") or "").strip()
        auth_type = form.get("auth_type") or ""
        secret = (form.get("secret") or "").encode("utf-8")
        passphrase = (form.get("passphrase") or "").encode("utf-8")
        try:
            port = int((form.get("port") or "").strip())
        except ValueError:
            port = 0
        if port == 0:
            port = 22

        if (not name or not host or not username
                or auth_type not in ("key", "password") or not secret):
            return redirect("/?err=" + quote(
                "Fill in name, host, username, auth type and the credential."))
        if auth_type == "key" and secret and not secret.endswith(b"\n"):
            secret += b"\n"
        try:
            store.create_ssh_server(name, host, port, username, auth_type, secret, passphrase)
        except Exception as e:  # noqa: BLE001
            log.error("create server: %s", e)
            return redirect("/?err=" + quote("Could not save the connection."))
        return redirect("/")

    @app.get("/servers/{id}")
    async def server_detail(request: Request, id: str):
        sv = store.get_ssh_server(id)
        if sv is None:
            return PlainTextResponse("not found", 404)
        data = shell("servers", id)
        data["server"] = sv
        data["sessions"] = store.list_sessions(id)
        data["command_groups"] = COMMAND_GROUPS
        return render("detail.html", request, data)

    @app.get("/servers/{id}/edit")
    async def edit_server_form(request: Request, id: str):
        sv = store.get_ssh_server(id)
        if sv is None:
            return PlainTextResponse("not found", 404)
        data = shell("servers", id)
        data["server"] = sv
        return render("edit.html", request, data)

    @app.post("/servers/{id}/edit")
    async def edit_server(request: Request, id: str):
        form = await request.form()
        name = (form.get("name") or "").strip()
        host = (form.get("host") or "").strip()
        username = (form.get("username") or "").strip()
        auth_type = form.get("auth_type") or ""
        secret = (form.get("secret") or "").encode("utf-8")
        passphrase = (form.get("passphrase") or "").encode("utf-8")
        try:
            port = int((form.get("port") or "").strip())
        except ValueError:
            port = 0
        if port == 0:
            port = 22
        reset_host_key = bool(form.get("reset_host_key"))

        if not name or not host or not username or auth_type not in ("key", "password"):
            return redirect(f"/servers/{id}/edit")
        if auth_type == "key" and secret and not secret.endswith(b"\n"):
            secret += b"\n"
        try:
            store.update_ssh_server(id, name, host, port, username, auth_type,
                                    secret, passphrase, reset_host_key)
        except Exception as e:  # noqa: BLE001
            log.error("update server: %s", e)
        return redirect(f"/servers/{id}")

    @app.post("/servers/{id}/rename")
    async def rename_server(request: Request, id: str):
        form = await request.form()
        name = (form.get("name") or "").strip()
        if not name:
            return redirect(f"/servers/{id}")
        try:
            store.rename_ssh_server(id, name)
        except Exception as e:  # noqa: BLE001
            log.error("rename server: %s", e)
        back = form.get("back") or f"/servers/{id}"
        return redirect(back)

    @app.post("/servers/{id}/delete")
    async def delete_server(request: Request, id: str):
        try:
            store.delete_ssh_server(id)
        except Exception as e:  # noqa: BLE001
            log.error("delete server: %s", e)
        return redirect("/")

    @app.post("/servers/{id}/test")
    async def test_server(request: Request, id: str):
        try:
            await manager.test_connection(id)
        except Exception as e:  # noqa: BLE001
            log.info("test connection failed server=%s err=%s", id, e)
        return redirect(f"/servers/{id}")

    @app.post("/servers/{id}/run")
    async def run_command(request: Request, id: str):
        form = await request.form()
        cmd = (form.get("command") or "").strip()
        if store.get_ssh_server(id) is None:
            return PlainTextResponse("not found", 404)
        data: dict = {}
        if cmd:
            try:
                out, err = await manager.run_command(id, cmd, timeout=60)
            except Exception as e:  # noqa: BLE001 — connection-level failure
                out, err = "", str(e)
            data["last_command"] = cmd
            if err:
                data["last_output"] = (out + "\n[error] " + err) if out else ("[error] " + err)
            elif not out.strip():
                data["last_output"] = "(no output)"
            else:
                data["last_output"] = out
        return render("frag_run_output.html", request, data)

    # --- files -------------------------------------------------------------

    @app.get("/servers/{id}/files")
    async def files_browse(request: Request, id: str):
        sv = store.get_ssh_server(id)
        if sv is None:
            return PlainTextResponse("not found", 404)
        dir = request.query_params.get("path", "")
        data = shell("servers", id)
        data["server"] = sv
        try:
            resolved, entries = await asyncio.wait_for(manager.list_dir(id, dir), timeout=30)
            data["path"] = resolved
            data["parent"] = remote_pkg.parent(resolved)
            data["crumbs"] = _breadcrumbs(resolved)
            data["entries"] = entries
        except Exception as e:  # noqa: BLE001
            data["path"] = dir or "/"
            data["parent"] = remote_pkg.parent(dir or "/")
            data["crumbs"] = _breadcrumbs(dir or "/")
            data["entries"] = []
            data["error"] = str(e)
        return render("files.html", request, data)

    @app.get("/servers/{id}/files/download")
    async def file_download(request: Request, id: str):
        remote_path = request.query_params.get("path", "")
        if not remote_path:
            return PlainTextResponse("path required", 400)
        try:
            size, agen = await manager.download(id, remote_path)
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("download failed: " + str(e), 502)
        name = posixpath.basename(remote_path).replace('"', "")
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        if size > 0:
            headers["Content-Length"] = str(size)
        return StreamingResponse(agen, media_type="application/octet-stream", headers=headers)

    @app.post("/servers/{id}/files/upload")
    async def file_upload(request: Request, id: str):
        form = await request.form()
        dir = form.get("dir") or ""
        files = form.getlist("file")
        files = [f for f in files if getattr(f, "filename", "")]
        if not files:
            return PlainTextResponse("no file provided", 400)
        for up in files:
            data = await up.read()
            dest = posixpath.join(dir, posixpath.basename(up.filename))
            try:
                await manager.upload(id, dest, data)
            except Exception as e:  # noqa: BLE001
                log.warning("sftp upload failed dest=%s err=%s", dest, e)
                return PlainTextResponse("upload failed: " + str(e), 502)
        return redirect_files(id, dir)

    @app.post("/servers/{id}/files/mkdir")
    async def file_mkdir(request: Request, id: str):
        form = await request.form()
        dir = form.get("dir") or ""
        name = (form.get("name") or "").strip()
        if not name:
            return PlainTextResponse("folder name required", 400)
        try:
            await manager.mkdir(id, posixpath.join(dir, name))
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("mkdir failed: " + str(e), 502)
        return redirect_files(id, dir)

    @app.post("/servers/{id}/files/newfile")
    async def file_new(request: Request, id: str):
        form = await request.form()
        dir = form.get("dir") or ""
        name = posixpath.basename((form.get("name") or "").strip())
        if not name or name in (".", "/"):
            return PlainTextResponse("file name required", 400)
        dest = posixpath.join(dir, name)
        try:
            await manager.create_file(id, dest)
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("create file failed: " + str(e), 502)
        return redirect(f"/servers/{id}/files/edit?path={quote(dest)}")

    @app.post("/servers/{id}/files/rename")
    async def file_rename(request: Request, id: str):
        form = await request.form()
        dir = form.get("dir") or ""
        target = (form.get("path") or "").strip()
        new_name = posixpath.basename((form.get("newname") or "").strip())
        if not target or not new_name or new_name == ".":
            return PlainTextResponse("path and new name required", 400)
        dest = posixpath.join(posixpath.dirname(target), new_name)
        try:
            await manager.rename(id, target, dest)
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("rename failed: " + str(e), 502)
        return redirect_files(id, dir)

    @app.post("/servers/{id}/files/delete")
    async def file_delete(request: Request, id: str):
        form = await request.form()
        dir = form.get("dir") or ""
        try:
            target = resolve_delete_target(form.get("path") or "")
        except ValueError as e:
            return PlainTextResponse(str(e), 400)
        try:
            await manager.remove(id, target)
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("delete failed: " + str(e), 502)
        return redirect_files(id, dir)

    @app.get("/servers/{id}/files/edit")
    async def file_edit_form(request: Request, id: str):
        sv = store.get_ssh_server(id)
        if sv is None:
            return PlainTextResponse("not found", 404)
        remote_path = request.query_params.get("path", "")
        if not remote_path:
            return PlainTextResponse("path required", 400)
        data = shell("servers", id)
        data["server"] = sv
        data["path"] = remote_path
        data["dir"] = posixpath.dirname(remote_path)
        data["name"] = posixpath.basename(remote_path)
        try:
            body = await asyncio.wait_for(manager.read_text(id, remote_path), timeout=30)
            data["content"] = body.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            data["error"] = str(e)
        return render("fileedit.html", request, data)

    @app.post("/servers/{id}/files/edit")
    async def file_edit_save(request: Request, id: str):
        form = await request.form()
        remote_path = (form.get("path") or "").strip()
        if not remote_path:
            return PlainTextResponse("path required", 400)
        content = (form.get("content") or "").encode("utf-8")
        try:
            await asyncio.wait_for(manager.upload(id, remote_path, content), timeout=60)
        except Exception as e:  # noqa: BLE001
            return PlainTextResponse("save failed: " + str(e), 502)
        return redirect_files(id, posixpath.dirname(remote_path))

    # --- recordings --------------------------------------------------------

    @app.get("/recordings")
    async def list_recordings(request: Request):
        sessions = store.list_sessions("")
        order: list[str] = []
        by_server: dict[str, dict] = {}
        for ss in sessions:
            g = by_server.get(ss.server_id)
            if g is None:
                g = {"server_id": ss.server_id, "server_name": ss.server_name, "sessions": []}
                by_server[ss.server_id] = g
                order.append(ss.server_id)
            g["sessions"].append(ss)
        groups = [by_server[i] for i in order]
        data = shell("recordings", "")
        data["groups"] = groups
        data["total"] = len(sessions)
        return render("recordings.html", request, data)

    @app.get("/recordings/{id}")
    async def replay_recording(request: Request, id: str):
        sess, transcript = store.get_session(id)
        if sess is None:
            return PlainTextResponse("not found", 404)
        data = shell("recordings", sess.server_id)
        data["session"] = sess
        data["recording"] = transcript
        if sess.ended_at is not None:
            data["duration_label"] = _fmt_duration((sess.ended_at - sess.started_at).total_seconds())
        return render("replay.html", request, data)

    @app.post("/recordings/{id}/delete")
    async def delete_recording(request: Request, id: str):
        try:
            store.delete_session(id)
        except Exception as e:  # noqa: BLE001
            log.error("delete recording: %s", e)
        form = await request.form()
        back = form.get("back") or "/recordings"
        return redirect(back)

    @app.post("/recordings/delete")
    async def delete_recordings_bulk(request: Request):
        form = await request.form()
        for sid in form.getlist("session"):
            try:
                store.delete_session(sid)
            except Exception as e:  # noqa: BLE001
                log.warning("bulk delete recording id=%s err=%s", sid, e)
        back = form.get("back") or "/recordings"
        return redirect(back)

    # --- terminal WebSocket ------------------------------------------------

    @app.websocket("/servers/{id}/terminal/ws")
    async def terminal_ws(websocket: WebSocket, id: str):
        await websocket.accept()
        websocket._sc_released = False  # local flag

        async def wtext(msg: str):
            try:
                await websocket.send_text(msg)
            except Exception:
                pass

        async def release():
            if getattr(websocket, "_sc_released", False):
                return
            websocket._sc_released = True
            async with term_lock:
                term_state["live"] -= 1

        async with term_lock:
            if cfg.max_terminals > 0 and term_state["live"] >= cfg.max_terminals:
                await wtext("\r\n*** too many terminals open (SSHCONSOLE_MAX_TERMINALS). "
                            "Close one and retry. ***\r\n")
                await websocket.close(code=1013)
                return
            term_state["live"] += 1

        try:
            try:
                conn, meta = await manager.connect(id)
            except Exception as e:  # noqa: BLE001
                await wtext("\r\n*** connection failed: " + str(e) + " ***\r\n")
                await websocket.close()
                return

            rec = TermRecorder(_RECORD_CAP)
            rec_id = store.create_terminal_session(id, meta.name)
            try:
                async with conn.create_process(term_type="xterm-256color",
                                               term_size=(80, 24), encoding=None) as proc:
                    log.info("terminal opened server=%s host=%s", id, meta.host)
                    stop = asyncio.Event()

                    async def pump_out():
                        try:
                            while True:
                                data = await proc.stdout.read(8192)
                                if not data:
                                    break
                                rec.write(data)
                                await websocket.send_bytes(data)
                        except Exception:
                            pass
                        finally:
                            stop.set()

                    async def pump_in():
                        try:
                            while True:
                                msg = await websocket.receive()
                                if msg.get("type") == "websocket.disconnect":
                                    break
                                text = msg.get("text")
                                if text is not None:
                                    if text and text[0] == "{":
                                        try:
                                            ctl = json.loads(text)
                                            cols, rows = int(ctl.get("cols", 0)), int(ctl.get("rows", 0))
                                            if cols > 0 and rows > 0:
                                                proc.change_terminal_size(cols, rows)
                                                continue
                                        except Exception:
                                            pass
                                    proc.stdin.write(text.encode("utf-8"))
                                elif msg.get("bytes") is not None:
                                    proc.stdin.write(msg["bytes"])
                        except Exception:
                            pass
                        finally:
                            stop.set()

                    out_task = asyncio.create_task(pump_out())
                    in_task = asyncio.create_task(pump_in())
                    await stop.wait()
                    for t in (out_task, in_task):
                        t.cancel()
                    await asyncio.gather(out_task, in_task, return_exceptions=True)
            finally:
                conn.close()
                total, out = rec.result()
                try:
                    store.finish_terminal_session(rec_id, total, out)
                except Exception as e:  # noqa: BLE001
                    log.warning("save recording session=%s err=%s", rec_id, e)
            try:
                await websocket.close()
            except Exception:
                pass
        finally:
            await release()

    return app
