#!/usr/bin/env python3
"""Reliable backup of cust_* to E:, then run v4 reimport."""
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
LOCAL_SH = Path(r"d:\nodejs\newapi\new-api\.tmp_server_backup.sh")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=25, allow_agent=False, look_for_keys=False)


def run(cmd, timeout=600):
    print("\n$", cmd[:240], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    safe = (out[-15000:] if len(out) > 15000 else out).replace("\xa5", "Y")
    if safe.strip():
        print(safe, flush=True)
    if err.strip():
        e2 = "\n".join(ln for ln in err.splitlines() if "Using a password" not in ln and "Warning" not in ln)
        if e2.strip():
            print("ERR", e2[-2000:], flush=True)
    print("exit", code, flush=True)
    return code, out


BACKUP_DIR.mkdir(parents=True, exist_ok=True)
print("BACKUP_DIR", BACKUP_DIR, flush=True)

# ensure no generator running
run("pkill -9 -f gen_on_server_v4 2>/dev/null; sleep 1; pgrep -af gen_on_server || echo NO_GEN")

# snapshot counts
code, snap = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT \"USERS\"; SELECT username, id, request_count, used_quota FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT \"LOGS\"; SELECT username, COUNT(*) FROM logs WHERE username LIKE \"cust_%\" GROUP BY username ORDER BY username;
SELECT \"TOTAL\", COUNT(*) FROM logs WHERE username LIKE \"cust_%\";
"' 2>/dev/null""",
    timeout=600,
)
(BACKUP_DIR / "pre_backup_snapshot.txt").write_text(snap, encoding="utf-8")
run("df -h / | tee /tmp/df_pre.txt")

# upload and run backup script on server (writes to /tmp/settlement_bak)
sftp = c.open_sftp()
sftp.put(str(LOCAL_SH), "/tmp/server_backup.sh")
sftp.close()
run("chmod +x /tmp/server_backup.sh")

print("=== running server backup (logs gzip may take 30-90min) ===", flush=True)
# Need MYSQL_ROOT_PASSWORD inside script - source from container env via wrapper
code, out = run(
    'rm -rf /tmp/settlement_bak; mkdir -p /tmp/settlement_bak; '
    'PASS=$(docker exec mysql printenv MYSQL_ROOT_PASSWORD); '
    'export MYSQL_ROOT_PASSWORD="$PASS"; '
    'bash /tmp/server_backup.sh',
    timeout=10800,
)
if "BACKUP_OK" not in out and code != 0:
    print("BACKUP FAILED - abort reimport", flush=True)
    c.close()
    sys.exit(1)

# download backup files to E:
print("=== downloading backup to local E: ===", flush=True)
sftp = c.open_sftp()
remote_files = sftp.listdir("/tmp/settlement_bak")
print("remote files", remote_files, flush=True)
for name in remote_files:
    rpath = f"/tmp/settlement_bak/{name}"
    lpath = BACKUP_DIR / name
    print(f"get {name} ...", flush=True)
    t0 = time.time()
    sftp.get(rpath, str(lpath))
    sz = lpath.stat().st_size
    print(f"  {name}: {sz/1024/1024:.1f} MB in {time.time()-t0:.0f}s", flush=True)
sftp.close()

# verify logs gz size
logs_gz = BACKUP_DIR / "logs_cust.sql.gz"
if not logs_gz.exists() or logs_gz.stat().st_size < 10_000_000:
    print("ERROR: logs backup too small:", logs_gz, flush=True)
    # still free server space but DO NOT wipe
    run("ls -lh /tmp/settlement_bak")
    c.close()
    sys.exit(2)

meta = {
    "backup_dir": str(BACKUP_DIR),
    "files": {p.name: p.stat().st_size for p in BACKUP_DIR.iterdir()},
    "created_at": datetime.now().isoformat(timespec="seconds"),
}
(BACKUP_DIR / "backup_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
print("backup_meta", meta, flush=True)

# free server backup space before wipe+import
run("rm -rf /tmp/settlement_bak; df -h / | tail -1")

# upload generator
sftp = c.open_sftp()
sftp.put(str(LOCAL_SPEC), "/tmp/settlement_spec_v4.json")
sftp.put(str(LOCAL_GEN), "/tmp/gen_on_server_v4.py")
sftp.close()
run("python3 -c 'import bcrypt' 2>/dev/null || pip3 install bcrypt -q")

print("=== start v4 import (wipe + 4 customers) ===", flush=True)
run(
    "rm -f /tmp/settlement_v4_summary.json /tmp/settlement_v4_run.log; mkdir -p /tmp/settlement_v4; "
    "setsid env SETTLEMENT_MIN_FREE_GB=5 python3 -u /tmp/gen_on_server_v4.py "
    "> /tmp/settlement_v4_run.log 2>&1 < /dev/null & echo PID:$!; sleep 2; "
    "pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' || echo START_FAIL",
    timeout=30,
)

for i in range(480):
    time.sleep(30)
    code, out = run(
        "tail -n 20 /tmp/settlement_v4_run.log 2>/dev/null; echo ---; "
        "if pgrep -af '^python3 -u /tmp/gen_on_server_v4.py' >/dev/null; then echo RUNNING; else echo DONE_PROC; fi; "
        "if test -f /tmp/settlement_v4_summary.json; then echo HAS_SUMMARY; fi; "
        "df -h / | awk 'NR==2{print $3,$4,$5}'",
        timeout=120,
    )
    print(f"=== poll {i+1} ===", flush=True)
    if "HAS_SUMMARY" in out and "DONE_PROC" in out:
        break
    if "DONE_PROC" in out and "HAS_SUMMARY" not in out:
        print("IMPORT FAILED", flush=True)
        run("tail -n 150 /tmp/settlement_v4_run.log")
        break
else:
    print("TIMEOUT", flush=True)

run("sudo systemctl restart new-api; sleep 2; curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status")
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
(BACKUP_DIR / "post_import_verify.txt").write_text(verify, encoding="utf-8")
run("cat /tmp/settlement_v4_summary.json 2>/dev/null || echo NO_SUMMARY")
try:
    sftp = c.open_sftp()
    sftp.get("/tmp/settlement_v4_summary.json", str(BACKUP_DIR / "settlement_v4_summary.json"))
    sftp.close()
except Exception as e:
    print("summary pull", e, flush=True)

c.close()
print("ALL DONE backup=", BACKUP_DIR, flush=True)
