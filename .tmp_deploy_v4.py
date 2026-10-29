#!/usr/bin/env python3
"""Backup old cust_* settlement data to local E:, then reimport v4."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import paramiko

HOST = "8.133.218.152"
USER = "ecs-user"
PASSWORD = "e4bA5e$27c4!"
BACKUP_DIR = Path(r"E:\settlement_backup") / datetime.now().strftime("backup_%Y%m%d_%H%M%S")
LOCAL_SPEC = Path(r"d:\nodejs\newapi\new-api\.tmp_settlement_gen\settlement_spec_v4.json")
LOCAL_GEN = Path(r"d:\nodejs\newapi\new-api\.tmp_gen_on_server_v4.py")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=25, allow_agent=False, look_for_keys=False)


def run(cmd, timeout=600):
    print("\n$", cmd[:220], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    safe = (out[-12000:] if len(out) > 12000 else out).replace("\xa5", "Y")
    if safe.strip():
        print(safe, flush=True)
    if err.strip():
        # filter mysql password warnings
        e2 = "\n".join(ln for ln in err.splitlines() if "Using a password" not in ln)
        if e2.strip():
            print("ERR", e2[-2000:].replace("\xa5", "Y"), flush=True)
    print("exit", code, flush=True)
    return code, out


def stream_dump(remote_cmd: str, local_path: Path, timeout: int = 7200):
    """Run remote command and stream stdout binary to local file."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n>> streaming dump → {local_path}", flush=True)
    transport = c.get_transport()
    chan = transport.open_session()
    chan.settimeout(timeout)
    chan.exec_command(remote_cmd)
    n = 0
    t0 = time.time()
    with local_path.open("wb") as f:
        while True:
            if chan.recv_ready():
                data = chan.recv(1024 * 256)
                if not data:
                    break
                f.write(data)
                n += len(data)
                if n % (50 * 1024 * 1024) < 256 * 1024:
                    print(f"  ... {n/1024/1024:.0f} MB in {time.time()-t0:.0f}s", flush=True)
            elif chan.exit_status_ready():
                # drain remaining
                while chan.recv_ready():
                    data = chan.recv(1024 * 256)
                    if not data:
                        break
                    f.write(data)
                    n += len(data)
                break
            else:
                time.sleep(0.05)
        # final drain
        while True:
            try:
                data = chan.recv(1024 * 256)
            except Exception:
                break
            if not data:
                break
            f.write(data)
            n += len(data)
    code = chan.recv_exit_status()
    print(f"  wrote {n/1024/1024:.1f} MB exit={code}", flush=True)
    return code, n


BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# --- 1) metadata snapshot ---
print("=== STEP1 metadata snapshot ===", flush=True)
code, out = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, id, request_count, used_quota, quota, display_name, remark FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT username, COUNT(*) c, COALESCE(SUM(quota),0) q FROM logs WHERE username LIKE \"cust_%\" AND type=2 GROUP BY username ORDER BY username;
SELECT COUNT(*) FROM logs;
DF_PLACEHOLDER
"' 2>/dev/null""".replace("DF_PLACEHOLDER", ""),
    timeout=300,
)
(BACKUP_DIR / "pre_wipe_users_logs.txt").write_text(out, encoding="utf-8")
run("df -h / | tee /tmp/df_before_backup.txt")

# --- 2) dump users / tokens (small) ---
print("=== STEP2 dump users+tokens ===", flush=True)
run(
    r"""docker exec mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick --no-tablespaces `new-api` users --where="username LIKE \"cust_%\""' > /tmp/bak_users.sql 2>/dev/null"""
)
run(
    r"""docker exec mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick --no-tablespaces `new-api` tokens --where="user_id IN (SELECT id FROM users WHERE username LIKE \"cust_%\")"' > /tmp/bak_tokens.sql 2>/dev/null"""
)
run(
    r"""docker exec mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick --no-tablespaces `new-api` quota_data --where="username LIKE \"cust_%\""' > /tmp/bak_quota_data.sql 2>/dev/null"""
)
sftp = c.open_sftp()
for name in ("bak_users.sql", "bak_tokens.sql", "bak_quota_data.sql"):
    try:
        sftp.get(f"/tmp/{name}", str(BACKUP_DIR / name))
        print("got", name, flush=True)
    except Exception as e:
        print("skip", name, e, flush=True)
sftp.close()

# --- 3) stream logs dump gzipped to local E: ---
print("=== STEP3 stream logs dump (gzip) ===", flush=True)
# write dump on server to /tmp with gzip - may need space; use streaming without large intermediate if possible
# Approach: mysqldump | gzip -c streamed over SSH
dump_cmd = (
    'docker exec mysql sh -c '
    '\'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --quick --no-tablespaces '
    '--hex-blob `new-api` logs --where="username LIKE \\"cust_%\\""\' 2>/dev/null | gzip -c'
)
code, nbytes = stream_dump(dump_cmd, BACKUP_DIR / "logs_cust.sql.gz", timeout=10800)
meta = {
    "backup_dir": str(BACKUP_DIR),
    "logs_gz_bytes": nbytes,
    "logs_gz_exit": code,
    "created_at": datetime.now().isoformat(timespec="seconds"),
}
(BACKUP_DIR / "backup_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
if code != 0 or nbytes < 1000:
    print("WARN: logs dump may be incomplete; continuing after confirming size", flush=True)
print("backup_meta", meta, flush=True)

# free server temp
run("rm -f /tmp/bak_users.sql /tmp/bak_tokens.sql /tmp/bak_quota_data.sql; df -h / | tail -1")

# --- 4) upload v4 assets ---
print("=== STEP4 upload generator ===", flush=True)
sftp = c.open_sftp()
sftp.put(str(LOCAL_SPEC), "/tmp/settlement_spec_v4.json")
sftp.put(str(LOCAL_GEN), "/tmp/gen_on_server_v4.py")
sftp.close()
run("python3 -c 'import bcrypt' 2>/dev/null || pip3 install bcrypt -q")
run("pkill -f '/tmp/gen_on_server_v4.py' 2>/dev/null; rm -f /tmp/settlement_v4_summary.json /tmp/settlement_v4_run.log; mkdir -p /tmp/settlement_v4; sync")

# --- 5) start generator (wipes cust_% then imports) ---
print("=== STEP5 start v4 import ===", flush=True)
run(
    "setsid env SETTLEMENT_MIN_FREE_GB=5 python3 -u /tmp/gen_on_server_v4.py "
    "> /tmp/settlement_v4_run.log 2>&1 < /dev/null & echo PID:$!; sleep 2; "
    "pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' || echo START_FAIL",
    timeout=30,
)

for i in range(480):  # up to ~4h
    time.sleep(30)
    code, out = run(
        "tail -n 18 /tmp/settlement_v4_run.log 2>/dev/null; echo ---; "
        "if pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' >/dev/null; then echo RUNNING; else echo DONE_PROC; fi; "
        "if test -f /tmp/settlement_v4_summary.json; then echo HAS_SUMMARY; fi; "
        "df -h / | awk 'NR==2{print $3,$4,$5}'",
        timeout=120,
    )
    print(f"=== poll {i+1} ===", flush=True)
    if "HAS_SUMMARY" in out and "DONE_PROC" in out:
        break
    if "DONE_PROC" in out and "HAS_SUMMARY" not in out:
        print("FAILED", flush=True)
        run("tail -n 120 /tmp/settlement_v4_run.log")
        break
else:
    print("TIMEOUT", flush=True)

run("sudo systemctl restart new-api; sleep 2; curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status")
code, out = run(
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
(BACKUP_DIR / "post_import_verify.txt").write_text(out, encoding="utf-8")
run("cat /tmp/settlement_v4_summary.json 2>/dev/null || echo NO_SUMMARY")

# pull summary locally
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/settlement_v4_summary.json", str(BACKUP_DIR / "settlement_v4_summary.json"))
    sftp.close()
except Exception as e:
    print("summary pull fail", e, flush=True)

c.close()
print("BACKUP_DIR", BACKUP_DIR, flush=True)
print("DEPLOY V4 FINISHED", flush=True)
