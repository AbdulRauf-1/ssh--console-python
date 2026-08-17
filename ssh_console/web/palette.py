"""The "Commands you can run" palette on the connection detail page: a curated set
of useful, non-interactive commands the user can click to load into the run box.
They are examples, not a restriction — any command that finishes on its own can be
typed.
"""

from __future__ import annotations

# Each group is {"title": str, "commands": [{"cmd": str, "help": str}, ...]}.
COMMAND_GROUPS = [
    {"title": "System", "commands": [
        {"cmd": "uname -a", "help": "Kernel and architecture"},
        {"cmd": "uptime", "help": "How long the box has been up, and load average"},
        {"cmd": "hostname", "help": "The server's hostname"},
        {"cmd": "cat /etc/os-release", "help": "Which distribution and version"},
        {"cmd": "whoami", "help": "Which user commands run as"},
    ]},
    {"title": "Services (systemd)", "commands": [
        {"cmd": "sudo systemctl status nginx", "help": "Is nginx running? (needs sudo)"},
        {"cmd": "sudo systemctl status ssh", "help": "Status of the SSH service"},
        {"cmd": "systemctl list-units --type=service --state=running", "help": "Every running service"},
        {"cmd": "sudo systemctl restart nginx", "help": "Restart nginx (needs sudo)"},
    ]},
    {"title": "Disk & memory", "commands": [
        {"cmd": "df -h", "help": "Free space per filesystem"},
        {"cmd": "free -h", "help": "Memory and swap usage"},
        {"cmd": "du -sh /var/* 2>/dev/null | sort -h | tail", "help": "Largest things under /var"},
    ]},
    {"title": "Processes", "commands": [
        {"cmd": "ps aux --sort=-%mem | head", "help": "Top memory-using processes"},
        {"cmd": "top -bn1 | head -15", "help": "A one-shot snapshot of top"},
    ]},
    {"title": "Network", "commands": [
        {"cmd": "ss -tulpn", "help": "Listening ports and what owns them"},
        {"cmd": "ip a", "help": "Network interfaces and addresses"},
        {"cmd": "curl -sI https://example.com", "help": "Check outbound HTTPS works"},
    ]},
    {"title": "Logs", "commands": [
        {"cmd": "journalctl -n 50 --no-pager", "help": "Last 50 journal lines"},
        {"cmd": "sudo tail -n 50 /var/log/syslog", "help": "Last 50 syslog lines (needs sudo)"},
    ]},
]
