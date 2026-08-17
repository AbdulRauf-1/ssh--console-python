# SSH Console (Python) — one small image on a slim Python base. No database service:
# storage is a local SQLite file kept in the /data volume. Runs as a non-root user
# and starts the app as a module (no launcher binary).

FROM python:3.12-slim

# Create a non-root user and its data dir up front.
RUN useradd --system --create-home --uid 65532 app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app

# Install dependencies first for better layer caching, then the source.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ssh_console ./ssh_console

USER app

ENV SSHCONSOLE_DATA_DIR=/data
# Bind to all interfaces INSIDE the container; the host port mapping decides what
# is actually reachable (publish to 127.0.0.1 to keep it local).
ENV SSHCONSOLE_LISTEN=0.0.0.0:8022
EXPOSE 8022
VOLUME ["/data"]
# Run as a module — no console-script / executable.
ENTRYPOINT ["python", "-m", "ssh_console"]
