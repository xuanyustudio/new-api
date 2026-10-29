#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152",username="ecs-user",password="e4bA5e$27c4!",timeout=20,allow_agent=False,look_for_keys=False)
cmds=[
"pgrep -af 'retry_import|gen_on_server|wipe_' ; echo done",
r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "
SHOW FULL PROCESSLIST;
SHOW ENGINE INNODB STATUS\G
"' 2>/dev/null | head -120""",
"df -h / | tail -1",
]
for cmd in cmds:
    print("===CMD===")
    i,o,e=c.exec_command(cmd,timeout=60)
    print(o.read().decode(errors="replace")[-5000:])
c.close()
