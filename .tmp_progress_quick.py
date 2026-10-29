#!/usr/bin/env python3
import re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152",username="ecs-user",password="e4bA5e$27c4!",timeout=20,allow_agent=False,look_for_keys=False)
tr=c.get_transport();
if tr: tr.set_keepalive(15)

def run(cmd, timeout=60):
    i,o,e=c.exec_command(cmd,timeout=timeout)
    return o.read().decode(errors="replace")

print("df:", run("df -h / | awk 'NR==2{print $3,$4,$5}'").strip())
print("new-api:", run("systemctl is-active new-api").strip())
print("gen:", run("pgrep -af '^python3 -u /tmp/gen_on_server_v4.py'; echo exit:$?").strip())
st=run(r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW ENGINE INNODB STATUS\G"' 2>/dev/null""")
for line in st.splitlines():
    if "ROLLING BACK" in line or "undo log entries" in line or ("ACTIVE" in line and "rollback" in line):
        print(line.strip())
pl=run(r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, time, state, LEFT(IFNULL(info,\"\"),70) FROM information_schema.processlist WHERE info LIKE \"%logs%\" OR state LIKE \"%rollback%\";
"' 2>/dev/null""")
print("processlist:\n", pl.strip() or "(none)")
c.close()
