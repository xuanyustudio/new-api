#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152", username="ecs-user", password="e4bA5e$27c4!", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=120):
    print("$", cmd[:200], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    print(out, flush=True)
    print("exit", stdout.channel.recv_exit_status(), flush=True)
    return out

# restore service NOW with existing binary
run("sudo systemctl start new-api; sleep 2; systemctl is-active new-api; curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status")

# find go
run("ls /usr/local/go/bin/go 2>/dev/null; ls ~/go/bin/go 2>/dev/null; ls /home/ecs-user/go/bin/go 2>/dev/null; type go 2>/dev/null; echo PATH=$PATH; cat /opt/new-api/.bashrc 2>/dev/null | head -5; grep -r go /home/ecs-user/.bashrc /home/ecs-user/.profile /etc/profile.d 2>/dev/null | head -20")
run("find /usr/local /home/ecs-user /opt -name 'go' -type f 2>/dev/null | head -20")
c.close()
