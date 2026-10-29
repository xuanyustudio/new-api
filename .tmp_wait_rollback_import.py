#!/usr/bin/env python3
"""Wait for InnoDB rollback to finish, then chunk-delete cust logs and import v4."""
from __future__ import annotations

import re
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
    print("\n$", cmd[:200], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out[-8000:].replace("\xa5", "Y"), flush=True)
    e2 = "\n".join(x for x in err.splitlines() if "password" not in x.lower())
    if e2.strip():
        print("ERR", e2[-800:], flush=True)
    print("exit", code, flush=True)
    return code, out


def mysql(sql: str, timeout=600):
    sftp = c.open_sftp()
    with sftp.file("/tmp/_q.sql", "w") as f:
        f.write(sql)
    sftp.close()
    return run(
        'docker cp /tmp/_q.sql mysql:/tmp/_q.sql && '
        'docker exec mysql sh -c \'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 < /tmp/_q.sql\'',
        timeout=timeout,
    )


run("sudo systemctl stop new-api; true")
run("pkill -9 -f 'wipe_cust_' 2>/dev/null; pkill -9 -f gen_on_server_v4 2>/dev/null; true")

# kill only WAITING deletes, never touch rolling-back trx by force if possible
# KILL on rollback trx just continues rollback; kill waiting ones
code, out = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, time, state, LEFT(info,80) FROM information_schema.processlist WHERE db=\"new-api\" AND command<>\"Daemon\";
"' 2>/dev/null"""
)
for line in out.strip().splitlines():
    parts = line.split("\t")
    if not parts or not parts[0].isdigit():
        continue
    pid = parts[0]
    info = line.lower()
    # kill waiting updates, not the rolling back one (state may say query end / rollback)
    if "waiting" in info or ("delete from logs" in info and "like" not in info):
        # don't kill the LIKE 'cust_%' rollback source if still rolling
        if "like" in info:
            print("leave rollback trx", pid, flush=True)
            continue
        run(f"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "KILL {pid};"' 2>/dev/null""")

print("=== waiting for rollback to finish ===", flush=True)
for i in range(240):  # up to ~2h
    code, st = run(
        r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW ENGINE INNODB STATUS\G"' 2>/dev/null | grep -E "ROLLING BACK|undo log entries|ACTIVE .* rollback|LIST OF TRANSACTIONS" | head -20""",
        timeout=60,
    )
    code2, pl = run(
        r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, time, state, LEFT(IFNULL(info,\"\"),60) FROM information_schema.processlist WHERE info LIKE \"%DELETE%logs%\" OR state LIKE \"%rollback%\";
"' 2>/dev/null""",
        timeout=60,
    )
    rolling = "ROLLING BACK" in st or "rollback" in (pl + st).lower()
    print(f"poll_rollback {i+1} rolling={rolling}", flush=True)
    if not rolling and "DELETE FROM logs" not in pl:
        # double-check no active trx
        if "ROLLING BACK" not in st:
            print("rollback finished", flush=True)
            break
    time.sleep(30)
else:
    print("WARN: rollback still going; continue carefully", flush=True)

# chunked delete by primary key ranges to avoid huge trx
print("=== chunked delete ===", flush=True)
mysql(
    """
USE `new-api`;
SELECT MIN(id), MAX(id), COUNT(*) FROM logs WHERE username LIKE 'cust_%';
""",
    timeout=600,
)

# Use batched deletes via stored loop in SQL
mysql(
    """
SET SESSION innodb_lock_wait_timeout=600;
USE `new-api`;
DROP PROCEDURE IF EXISTS wipe_cust_logs;
DELIMITER $$
CREATE PROCEDURE wipe_cust_logs()
BEGIN
  DECLARE n INT DEFAULT 1;
  WHILE n > 0 DO
    DELETE FROM logs WHERE username LIKE 'cust_%' LIMIT 50000;
    SET n = ROW_COUNT();
  END WHILE;
END$$
DELIMITER ;
CALL wipe_cust_logs();
DROP PROCEDURE wipe_cust_logs;
DELETE FROM quota_data WHERE username LIKE 'cust_%';
DELETE FROM tokens WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE 'cust_%') t);
DELETE FROM users WHERE username LIKE 'cust_%';
SELECT 'remain_logs', COUNT(*) FROM logs WHERE username LIKE 'cust_%';
SELECT 'remain_users', COUNT(*) FROM users WHERE username LIKE 'cust_%';
PURGE BINARY LOGS BEFORE NOW();
""",
    timeout=14400,
)

# DELIMITER may not work via mysql < file the same way - use python loop instead if failed
code, out = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`; SELECT COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
"' 2>/dev/null"""
)
remain = 0
try:
    remain = int(out.strip().splitlines()[-1])
except Exception:
    remain = -1
print("remain_logs", remain, flush=True)

if remain > 0:
    print("fallback batch delete loop", flush=True)
    for bi in range(500):
        code, o = mysql(
            """
SET SESSION innodb_lock_wait_timeout=600;
USE `new-api`;
DELETE FROM logs WHERE username LIKE 'cust_%' LIMIT 50000;
SELECT ROW_COUNT();
""",
            timeout=1800,
        )
        try:
            rc = int([ln for ln in o.strip().splitlines() if ln.strip().isdigit()][-1])
        except Exception:
            rc = -1
        print(f"batch {bi+1} deleted={rc}", flush=True)
        if rc == 0:
            break
        if bi % 10 == 0:
            run("df -h / | awk 'NR==2{print}'")
    mysql(
        """
USE `new-api`;
DELETE FROM quota_data WHERE username LIKE 'cust_%';
DELETE FROM tokens WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE 'cust_%') t);
DELETE FROM users WHERE username LIKE 'cust_%';
SELECT COUNT(*) FROM logs WHERE username LIKE 'cust_%';
SELECT COUNT(*) FROM users WHERE username LIKE 'cust_%';
PURGE BINARY LOGS BEFORE NOW();
""",
        timeout=3600,
    )

run("df -h / | tail -1")

# import
sftp = c.open_sftp()
sftp.put(str(LOCAL_SPEC), "/tmp/settlement_spec_v4.json")
sftp.put(str(LOCAL_GEN), "/tmp/gen_on_server_v4.py")
sftp.close()
run("python3 -c 'import bcrypt' 2>/dev/null || pip3 install bcrypt -q")

print("=== start import ===", flush=True)
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
"' 2>/dev/null""",
    timeout=600,
)
(BACKUP_DIR / "post_import_verify_final.txt").write_text(verify, encoding="utf-8")
run("cat /tmp/settlement_v4_summary.json 2>/dev/null || echo NO_SUMMARY")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/settlement_v4_summary.json", str(BACKUP_DIR / "settlement_v4_summary.json"))
    sftp.close()
except Exception as e:
    print("summary", e, flush=True)

c.close()
print("FINAL_DONE", flush=True)
