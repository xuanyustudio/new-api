#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("8.133.218.152",username="ecs-user",password="e4bA5e$27c4!",timeout=15,allow_agent=False,look_for_keys=False)
for cmd in ["ps aux | grep -E 'mysqldump|gzip|server_backup' | grep -v grep || echo NO_DUMP","ls -lh /tmp/settlement_bak 2>/dev/null || echo NO_DIR","df -h / | tail -1"]:
    print("===",cmd)
    i,o,e=c.exec_command(cmd,timeout=30)
    print(o.read().decode())
c.close()
