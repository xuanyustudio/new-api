#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152",username="ecs-user",password="e4bA5e$27c4!",timeout=20,allow_agent=False,look_for_keys=False)
cmds=[
"df -h / | tail -1",
"systemctl is-active new-api; pgrep -af gen_on_server; echo done",
r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, time, state, LEFT(IFNULL(info,\"\"),80) FROM information_schema.processlist WHERE db=\"new-api\" OR info LIKE \"%logs%\";
"' 2>/dev/null""",
r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW ENGINE INNODB STATUS\G"' 2>/dev/null | grep -E "ROLLING BACK|undo log entries|ACTIVE .* sec" | head -15""",
r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
SELECT username, request_count FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
"' 2>/dev/null""",
]
for cmd in cmds:
    print("===CMD===")
    i,o,e=c.exec_command(cmd,timeout=120)
    print(o.read().decode(errors="replace")[-3000:])
c.close()
