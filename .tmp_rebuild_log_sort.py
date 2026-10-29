#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152", username="ecs-user", password="e4bA5e$27c4!", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=900):
    print("$", cmd[:260], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    print(out[-8000:] if len(out) > 8000 else out, flush=True)
    code = stdout.channel.recv_exit_status()
    print("exit", code, flush=True)
    return code

# confirm source already patched
code = run("grep -n 'created_at desc' /opt/new-api/model/log.go | head -5")

# build with go in PATH, then restart
code = run(
    "export PATH=/usr/local/go/bin:$HOME/go/bin:$PATH && "
    "cd /opt/new-api && "
    "go version && "
    "sudo systemctl stop new-api && "
    "go build -o new-api . && "
    "sudo systemctl start new-api && "
    "sleep 2 && "
    "systemctl is-active new-api && "
    "curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status",
    timeout=900,
)

# verify binary contains new order string
run("strings /opt/new-api/new-api | grep -F 'created_at desc' | head -5")

c.close()
sys.exit(0 if code == 0 else 1)
