#!/usr/bin/env python3
"""Download server /tmp/settlement_bak to E:, then wipe+import v4."""
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
c.connect(HOST, username=USER, password=PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)
# keep alive during long SFTP
t = c.get_transport()
if t:
    t.set_keepalive(30)


def run(cmd, timeout=300):
    print("\n$", cmd[:200], flush=True)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out[-8000:].replace("\xa5", "Y"), flush=True)
    e2 = "\n".join(x for x in err.splitlines() if "password" not in x.lower() and "warning" not in x.lower())
    if e2.strip():
        print("ERR", e2[-1000:], flush=True)
    print("exit", code, flush=True)
    return code, out


BACKUP_DIR.mkdir(parents=True, exist_ok=True)
print("BACKUP_DIR", BACKUP_DIR, flush=True)

run("pkill -9 -f gen_on_server_v4 2>/dev/null; true")
code, out = run("ls -lh /tmp/settlement_bak; df -h / | tail -1")
if "logs_cust.sql.gz" not in out:
    print("FATAL: no server backup", flush=True)
    sys.exit(2)

# SFTP download with progress callback
sftp = c.open_sftp()


def make_cb(name):
    last = [0]

    def cb(transferred, total):
        if transferred - last[0] >= 50 * 1024 * 1024 or transferred == total:
            last[0] = transferred
            pct = 100.0 * transferred / total if total else 0
            print(f"  {name}: {transferred/1024/1024:.0f}/{total/1024/1024:.0f} MB ({pct:.0f}%)", flush=True)

    return cb


for name in sorted(sftp.listdir("/tmp/settlement_bak")):
    rpath = f"/tmp/settlement_bak/{name}"
    lpath = BACKUP_DIR / name
    st = sftp.stat(rpath)
    print(f"GET {name} ({st.st_size/1024/1024:.1f} MB)", flush=True)
    t0 = time.time()
    sftp.get(rpath, str(lpath), callback=make_cb(name))
    print(f"  done {lpath.stat().st_size/1024/1024:.1f} MB in {time.time()-t0:.0f}s", flush=True)
sftp.close()

logs = BACKUP_DIR / "logs_cust.sql.gz"
if logs.stat().st_size < 100_000_000:
    print("FATAL logs backup too small", logs.stat().st_size, flush=True)
    sys.exit(3)

meta = {
    "backup_dir": str(BACKUP_DIR),
    "files": {p.name: p.stat().st_size for p in BACKUP_DIR.iterdir() if p.is_file()},
    "created_at": datetime.now().isoformat(timespec="seconds"),
}
(BACKUP_DIR / "backup_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("backup_ok", meta, flush=True)

# free server backup space before wipe
run("rm -rf /tmp/settlement_bak; docker exec mysql sh -c 'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" -e \"PURGE BINARY LOGS BEFORE NOW();\"' 2>/dev/null; df -h / | tail -1")

sftp = c.open_sftp()
sftp.put(str(LOCAL_SPEC), "/tmp/settlement_spec_v4.json")
sftp.put(str(LOCAL_GEN), "/tmp/gen_on_server_v4.py")
sftp.close()
run("python3 -c 'import bcrypt' 2>/dev/null || pip3 install bcrypt -q")

print("=== start v4 import ===", flush=True)
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
        "tail -n 18 /tmp/settlement_v4_run.log 2>/dev/null; echo ---; "
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

run("sudo systemctl restart new-api; sleep 2; curl -sS -m 5 -o /dev/null -w 'status=%{http_code}\\n' http://127.0.0.1:3000/api/status")
code, verify = run(
    r"""docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "
USE \`new-api\`;
SELECT username, request_count, ROUND(used_quota/500000,2) FROM users WHERE username LIKE \"cust_%\" ORDER BY username;
SELECT username, COUNT(*) FROM logs WHERE username LIKE \"cust_%\" AND type=2 GROUP BY username ORDER BY username;
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
print("ALL_DONE", BACKUP_DIR, flush=True)
