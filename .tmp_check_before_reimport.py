#!/usr/bin/env python3
"""Check server disk + existing settlement users before backup/reimport."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "8.133.218.152",
    username="ecs-user",
    password="e4bA5e$27c4!",
    timeout=20,
    allow_agent=False,
    look_for_keys=False,
)

cmds = [
    "df -h / /tmp | tail -5",
    "du -sh /var/lib/docker/volumes 2>/dev/null; docker system df 2>/dev/null | head -10",
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, id, request_count, ROUND(used_quota/500000,2), ROUND(quota/500000,2) FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT COUNT(*) FROM logs;
SELECT ROUND(SUM(data_length+index_length)/1024/1024/1024,2) FROM information_schema.tables WHERE table_schema=\"new-api\" AND table_name=\"logs\";
"' 2>/dev/null""",
]
for cmd in cmds:
    print("===CMD===")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=120)
    print(stdout.read().decode("utf-8", errors="replace"))
c.close()
