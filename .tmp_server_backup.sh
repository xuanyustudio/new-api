#!/bin/bash
set -eu
PASS="${MYSQL_ROOT_PASSWORD}"
DB='new-api'
OUT=/tmp/settlement_bak
mkdir -p "$OUT"
echo "[1] dump users"
docker exec mysql mysqldump -uroot -p"$PASS" --single-transaction --quick --no-tablespaces \
  "$DB" users --where="username LIKE 'cust_%'" > "$OUT/users.sql"
wc -c "$OUT/users.sql"
echo "[2] dump tokens (all, small)"
docker exec mysql mysqldump -uroot -p"$PASS" --single-transaction --quick --no-tablespaces \
  "$DB" tokens > "$OUT/tokens.sql"
wc -c "$OUT/tokens.sql"
echo "[3] dump quota_data"
docker exec mysql mysqldump -uroot -p"$PASS" --single-transaction --quick --no-tablespaces \
  "$DB" quota_data --where="username LIKE 'cust_%'" > "$OUT/quota_data.sql"
wc -c "$OUT/quota_data.sql"
echo "[4] dump logs gzip (long) ..."
docker exec mysql mysqldump -uroot -p"$PASS" --single-transaction --quick --no-tablespaces \
  --hex-blob "$DB" logs --where="username LIKE 'cust_%'" | gzip -1 > "$OUT/logs_cust.sql.gz"
ls -lh "$OUT"
echo "BACKUP_OK"
