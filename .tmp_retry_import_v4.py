#!/usr/bin/env python3
"""Stop new-api, wipe cust_* safely, reimport v4. Backup already on E:."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import paramiko

HOST = "8.133.218.152"
USER = "ecs-user"
PASSWORD = "e4bA5e$27c4!"
LOCAL_SPEC = Path(r"d:\nodejs\newapi\new-api\.tmp_settlement_gen\settlement_spec_v4.json")
LOCAL_GEN = Path(r"d:\nodejs\newapi\new-api\.tmp_gen_on_server_v4.py")
BACKUP_DIR = Path(r"E:\settlement_backup\backup_20261029_191921")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)
tr = c.get_transport()
if tr:
    tr.set_keepalive(30)


def run(cmd, timeout=600):
    print("\n$", cmd[:220], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out[-12000:].replace("\xa5", "Y"), flush=True)
    e2 = "\n".join(x for x in err.splitlines() if "password" not in x.lower())
    if e2.strip():
        print("ERR", e2[-1200:], flush=True)
    print("exit", code, flush=True)
    return code, out


def mysql_file(sql: str, name: str, timeout: int = 3600):
    sftp = c.open_sftp()
    path = f"/tmp/{name}"
    with sftp.file(path, "w") as f:
        f.write(sql)
    sftp.close()
    return run(
        f"docker cp {path} mysql:{path} && "
        f"docker exec mysql sh -c 'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" --default-character-set=utf8mb4 < {path}'",
        timeout=timeout,
    )


run("sudo systemctl stop new-api; sleep 2; systemctl is-active new-api || echo STOPPED")
run("pkill -9 -f gen_on_server_v4 2>/dev/null; true")

# kill active queries holding locks
mysql_file(
    """
SELECT id, user, db, command, time, LEFT(info,120) FROM information_schema.processlist WHERE db='new-api';
""",
    "show_pl.sql",
    timeout=60,
)
mysql_file(
    """
SELECT CONCAT('KILL ', id, ';') INTO OUTFILE '/tmp/kill_queries.sql'
FROM information_schema.processlist
WHERE db='new-api' AND id <> CONNECTION_ID() AND command <> 'Daemon';
""",
    "gen_kills.sql",
    timeout=60,
)
# OUTFILE may fail due to secure_file_priv; use python-side kills instead
code, out = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id FROM information_schema.processlist WHERE db=\"new-api\" AND id<>CONNECTION_ID() AND user<>\"event_scheduler\";
"' 2>/dev/null"""
)
for line in out.strip().splitlines():
    pid = line.strip()
    if pid.isdigit():
        run(
            f"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "KILL {pid};"' 2>/dev/null""",
            timeout=30,
        )

print("=== wipe by username ===", flush=True)
usernames = [
    "cust_ahxt", "cust_bjyt", "cust_bjyt_use", "cust_yunlian", "cust_zjtt",
    "cust_hzqy", "cust_mgkj", "cust_szyt", "cust_tyww", "cust_xjyx", "cust_zlt",
]
for uname in usernames:
    print(f"-- wipe {uname}", flush=True)
    mysql_file(
        f"""
SET SESSION innodb_lock_wait_timeout=600;
USE `new-api`;
DELETE FROM logs WHERE username='{uname}';
DELETE FROM quota_data WHERE username='{uname}';
""",
        f"wipe_{uname}.sql",
        timeout=7200,
    )
    run("df -h / | awk 'NR==2{print}'")

mysql_file(
    """
SET SESSION innodb_lock_wait_timeout=600;
USE `new-api`;
DELETE FROM tokens WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE 'cust_%') t);
DELETE FROM users WHERE username LIKE 'cust_%';
DELETE FROM logs WHERE username LIKE 'cust_%';
DELETE FROM quota_data WHERE username LIKE 'cust_%';
SELECT 'remain_logs', COUNT(*) FROM logs WHERE username LIKE 'cust_%';
SELECT 'remain_users', COUNT(*) FROM users WHERE username LIKE 'cust_%';
PURGE BINARY LOGS BEFORE NOW();
""",
    "wipe_rest.sql",
    timeout=3600,
)
run("df -h / | tail -1")

sftp = c.open_sftp()
sftp.put(str(LOCAL_SPEC), "/tmp/settlement_spec_v4.json")
sftp.put(str(LOCAL_GEN), "/tmp/gen_on_server_v4.py")
sftp.close()
run("python3 -c 'import bcrypt' 2>/dev/null || pip3 install bcrypt -q")

print("=== start import (new-api still stopped) ===", flush=True)
run(
    "rm -f /tmp/settlement_v4_summary.json /tmp/settlement_v4_run.log; mkdir -p /tmp/settlement_v4; "
    "setsid env SETTLEMENT_MIN_FREE_GB=4 python3 -u /tmp/gen_on_server_v4.py "
    "> /tmp/settlement_v4_run.log 2>&1 < /dev/null & echo PID:$!; sleep 3; "
    "pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' || echo START_FAIL",
    timeout=30,
)

for i in range(500):
    time.sleep(45)
    code, out = run(
        "tail -n 20 /tmp/settlement_v4_run.log 2>/dev/null; echo ---; "
        "pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' >/dev/null && echo RUNNING || echo DONE_PROC; "
        "test -f /tmp/settlement_v4_summary.json && echo HAS_SUMMARY; "
        "df -h / | awk 'NR==2{print $3,$4,$5}'",
        timeout=120,
    )
    print(f"=== poll {i+1} ===", flush=True)
    if "HAS_SUMMARY" in out and "DONE_PROC" in out:
        break
    if "DONE_PROC" in out and "HAS_SUMMARY" not in out:
        print("IMPORT FAILED", flush=True)
        run("tail -n 200 /tmp/settlement_v4_run.log")
        break
else:
    print("TIMEOUT", flush=True)

run("sudo systemctl start new-api; sleep 3; curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status")
code, verify = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, request_count, ROUND(used_quota/500000,2) FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT username, COUNT(*) FROM logs WHERE username LIKE \"cust_%\" AND type=2 GROUP BY username ORDER BY username;
SELECT \"VIDEO\", username, ROUND(MIN(quota/500000),2), ROUND(AVG(quota/500000),2), ROUND(MAX(quota/500000),2), COUNT(*)
FROM logs WHERE username LIKE \"cust_%\" AND type=2 AND model_name LIKE \"%seedance%\" GROUP BY username;
SELECT \"TEXT\", username, ROUND(MIN(quota/500000),2), ROUND(AVG(quota/500000),2), ROUND(MAX(quota/500000),2), COUNT(*)
FROM logs WHERE username LIKE \"cust_%\" AND type=2 AND model_name NOT LIKE \"%seedance%\" GROUP BY username;
"' 2>/dev/null""",
    timeout=600,
)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
(BACKUP_DIR / "post_import_verify_retry.txt").write_text(verify, encoding="utf-8")
run("cat /tmp/settlement_v4_summary.json 2>/dev/null || echo NO_SUMMARY")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/settlement_v4_summary.json", str(BACKUP_DIR / "settlement_v4_summary.json"))
    sftp.close()
except Exception as e:
    print("summary", e, flush=True)

c.close()
print("RETRY_DONE", flush=True)
