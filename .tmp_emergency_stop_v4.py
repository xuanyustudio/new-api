#!/usr/bin/env python3
"""Emergency: stop v4 wipe/import, check state, redo proper backup."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152", username="ecs-user", password="e4bA5e$27c4!", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=120):
    print("\n$", cmd[:200])
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    print(out[-4000:] if len(out)>4000 else out)
    if err.strip() and "Using a password" not in err:
        print("ERR", err[-800:])
    print("exit", code)
    return code, out

# Kill import immediately
run("pkill -9 -f '/tmp/gen_on_server_v4.py' 2>/dev/null; pkill -9 -f 'gen_on_server_v4' 2>/dev/null; sleep 1; pgrep -af gen_on_server || echo NO_GEN")
run("tail -n 40 /tmp/settlement_v4_run.log 2>/dev/null || echo NO_LOG")
run(r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, request_count, ROUND(used_quota/500000,2) FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT username, COUNT(*) FROM logs WHERE username LIKE \"cust_%\" AND type=2 GROUP BY username ORDER BY username;
SELECT COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
"' 2>/dev/null""", timeout=300)
run("df -h / | tail -1")
c.close()
