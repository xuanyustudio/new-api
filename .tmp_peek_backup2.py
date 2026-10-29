#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152",username="ecs-user",password="e4bA5e$27c4!",timeout=15,allow_agent=False,look_for_keys=False)
cmds=[
"ps -ef | grep -E 'bash|docker|mysqldump|gzip|settlement' | grep -v grep | head -40",
"ls -lh /tmp/settlement_bak; zcat /tmp/settlement_bak/logs_cust.sql.gz 2>/dev/null | tail -c 200 | xxd | head -5; echo ---; zcat /tmp/settlement_bak/logs_cust.sql.gz 2>/dev/null | grep -c '^INSERT' || true; gzip -t /tmp/settlement_bak/logs_cust.sql.gz && echo GZIP_OK || echo GZIP_BAD",
]
for cmd in cmds:
    print("===CMD===")
    i,o,e=c.exec_command(cmd,timeout=120)
    print(o.read().decode(errors="replace")[-3000:])
    err=e.read().decode(errors="replace")
    if err.strip(): print("ERR",err[-500:])
c.close()
