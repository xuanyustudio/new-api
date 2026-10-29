#!/usr/bin/env python3
"""Patch log sort order on server and rebuild/restart."""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import paramiko

HOST = "8.133.218.152"
USER = "ecs-user"
PASSWORD = "e4bA5e$27c4!"
LOCAL_LOG = r"d:\nodejs\newapi\new-api\model\log.go"
REMOTE_LOG = "/opt/new-api/model/log.go"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=20, allow_agent=False, look_for_keys=False)


def run(cmd, timeout=600):
    print("\n$", cmd[:240], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out:
        print(out[-6000:] if len(out) > 6000 else out, flush=True)
    if err.strip():
        print("ERR", err[-1500:], flush=True)
    print("exit", code, flush=True)
    return code, out


# verify current order lines remotely before
run("grep -n 'Order(\"logs' /opt/new-api/model/log.go | head -20")

sftp = c.open_sftp()
sftp.put(LOCAL_LOG, REMOTE_LOG)
sftp.close()
print("uploaded log.go", flush=True)

run("grep -n 'created_at desc' /opt/new-api/model/log.go | head -10")

# find how binary is built/run
run("systemctl cat new-api | head -40")
run("ls -la /opt/new-api/new-api /opt/new-api/new-api-linux 2>/dev/null; which go; go version")

code, out = run(
    "cd /opt/new-api && "
    "sudo systemctl stop new-api && "
    "go build -o new-api . && "
    "sudo systemctl start new-api && "
    "sleep 2 && "
    "systemctl is-active new-api && "
    "curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status",
    timeout=600,
)

c.close()
print("DEPLOY SORT DONE", code, flush=True)
sys.exit(0 if code == 0 else 1)
