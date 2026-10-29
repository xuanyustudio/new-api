#!/usr/bin/env python3
"""Resilient: wait InnoDB rollback (reconnect on SSH drop), then batch-wipe + v4 import."""
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

c = None


def connect():
    global c
    if c:
        try:
            c.close()
        except Exception:
            pass
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)
    tr = c.get_transport()
    if tr:
        tr.set_keepalive(15)
    print("ssh connected", flush=True)


def run(cmd, timeout=90):
    global c
    for attempt in range(5):
        try:
            if c is None or c.get_transport() is None or not c.get_transport().is_active():
                connect()
            print("\n$", cmd[:180], flush=True)
            stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            if out.strip():
                print(out[-6000:].replace("\xa5", "Y"), flush=True)
            e2 = "\n".join(x for x in err.splitlines() if "password" not in x.lower())
            if e2.strip():
                print("ERR", e2[-600:], flush=True)
            print("exit", code, flush=True)
            return code, out
        except Exception as e:
            print(f"ssh error attempt {attempt+1}: {e}", flush=True)
            time.sleep(5)
            try:
                connect()
            except Exception as e2:
                print("reconnect fail", e2, flush=True)
                time.sleep(10)
    raise RuntimeError("ssh failed repeatedly")


def mysql_light(sql: str, timeout=120):
    # write via echo to avoid sftp issues during reconnect storms
    # use docker exec -e with heredoc through bash
    return run(
        "docker exec -i mysql sh -c 'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" --batch --raw --skip-column-names' <<'SQL'\n"
        + sql
        + "\nSQL",
        timeout=timeout,
    )


connect()
run("sudo systemctl stop new-api; true")
run("pkill -9 -f gen_on_server_v4 2>/dev/null; pkill -9 -f wipe_cust_ 2>/dev/null; true")

# kill any DELETE that is NOT the rolling-back LIKE query
code, out = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, LEFT(IFNULL(info,\"\"),100) FROM information_schema.processlist
WHERE info LIKE \"DELETE FROM logs%\" AND info NOT LIKE \"%LIKE%cust_%\";
"' 2>/dev/null"""
)
for line in out.strip().splitlines():
    pid = line.split("\t")[0].strip()
    if pid.isdigit():
        run(f"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "KILL {pid};"' 2>/dev/null""")

print("=== wait rollback ===", flush=True)
last_undo = None
for i in range(600):  # up to ~5h at 30s
    try:
        code, st = run(
            r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SHOW ENGINE INNODB STATUS\G"' 2>/dev/null | grep -E "ROLLING BACK|undo log entries|ACTIVE .* rollback" | head -5""",
            timeout=60,
        )
    except Exception as e:
        print("status fail", e, flush=True)
        time.sleep(30)
        continue
    m = None
    import re
    mm = re.search(r"undo log entries (\d+)", st)
    if mm:
        m = int(mm.group(1))
        delta = "" if last_undo is None else f" delta={last_undo-m}"
        print(f"rollback poll {i+1} undo={m}{delta}", flush=True)
        last_undo = m
    rolling = "ROLLING BACK" in st or "rollback" in st.lower()
    if not rolling:
        # confirm
        code, pl = run(
            r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SELECT id, state, LEFT(IFNULL(info,\"\"),60) FROM information_schema.processlist WHERE info LIKE \"%DELETE%logs%\";
"' 2>/dev/null""",
            timeout=60,
        )
        if not pl.strip():
            print("ROLLBACK DONE", flush=True)
            break
        if "LIKE" not in pl and "ROLLING" not in st:
            print("no rollback trx visible", flush=True)
            break
    time.sleep(30)
else:
    print("WARN still rolling after long wait; will try batch delete anyway", flush=True)

print("=== batch delete ===", flush=True)
for bi in range(800):
    code, o = run(
        r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
SET SESSION innodb_lock_wait_timeout=120;
USE \`new-api\`;
DELETE FROM logs WHERE username LIKE \"cust_%\" LIMIT 20000;
SELECT ROW_COUNT();
"' 2>/dev/null""",
        timeout=600,
    )
    try:
        rc = int([ln for ln in o.strip().splitlines() if ln.strip().lstrip("-").isdigit()][-1])
    except Exception:
        rc = -1
        print("batch parse fail:", o[-200:], flush=True)
    print(f"batch {bi+1} deleted={rc}", flush=True)
    if rc == 0:
        break
    if "Lock wait" in o or code != 0:
        print("lock/error, sleep and retry", flush=True)
        time.sleep(20)
    if bi % 20 == 0:
        run("df -h / | awk 'NR==2{print}'")

run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "
SET SESSION innodb_lock_wait_timeout=600;
USE \`new-api\`;
DELETE FROM quota_data WHERE username LIKE \"cust_%\";
DELETE FROM tokens WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE \"cust_%\") t);
DELETE FROM users WHERE username LIKE \"cust_%\";
SELECT \"remain_logs\", COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
SELECT \"remain_users\", COUNT(*) FROM users WHERE username LIKE \"cust_%\";
PURGE BINARY LOGS BEFORE NOW();
"' 2>/dev/null""",
    timeout=3600,
)
run("df -h / | tail -1")

# upload + import
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
    try:
        code, out = run(
            "tail -n 18 /tmp/settlement_v4_run.log 2>/dev/null; echo ---; "
            "pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' >/dev/null && echo RUNNING || echo DONE_PROC; "
            "test -f /tmp/settlement_v4_summary.json && echo HAS_SUMMARY; "
            "df -h / | awk 'NR==2{print $3,$4,$5}'",
            timeout=120,
        )
    except Exception as e:
        print("poll err", e, flush=True)
        continue
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
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
(BACKUP_DIR / "post_import_verify_final.txt").write_text(verify, encoding="utf-8")
run("cat /tmp/settlement_v4_summary.json 2>/dev/null || echo NO_SUMMARY")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/settlement_v4_summary.json", str(BACKUP_DIR / "settlement_v4_summary.json"))
    sftp.close()
except Exception as e:
    print("summary", e, flush=True)

try:
    c.close()
except Exception:
    pass
print("FINAL_DONE", flush=True)
