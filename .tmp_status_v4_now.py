#!/usr/bin/env python3
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

print("=== local backups ===")
root = Path(r"E:\settlement_backup")
if root.exists():
    for p in sorted(root.glob("backup_*")):
        files = {f.name: round(f.stat().st_size / 1024 / 1024, 1) for f in p.iterdir() if f.is_file()}
        print(p.name, files)
else:
    print("no local backup dir")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152", username="ecs-user", password="e4bA5e$27c4!", timeout=20, allow_agent=False, look_for_keys=False)

cmds = [
    "df -h / | tail -1",
    "pgrep -af gen_on_server; echo EXIT:$?",
    "ls -lh /tmp/settlement_bak 2>/dev/null; echo EXIT:$?",
    "test -f /tmp/settlement_v4_summary.json; echo SUMMARY:$?",
    "tail -n 20 /tmp/settlement_v4_run.log 2>/dev/null; echo EXIT:$?",
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, request_count FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
"' 2>/dev/null""",
]
for cmd in cmds:
    print("===CMD===")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=180)
    print(stdout.read().decode("utf-8", errors="replace"))
c.close()
