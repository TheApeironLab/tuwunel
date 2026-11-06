#!/usr/bin/env bash
# Tuwunel 在线备份：sidecar 给 tuwunel 发 SIGUSR2 → admin_signal_execute 执行 `server backup-database`
# （RocksDB BackupEngine，写进卷里的 /var/lib/tuwunel/backups，带校验与保留份数）→ 把备份仓库和媒体目录
# 打成 tar → age 加密 → s3://chentu-backup/matrix/<日期>/。在线备份不含媒体，所以媒体一起打包。
# 恢复步骤见 charts/tuwunel/README.md「恢复」。
# 需要: s5cmd、age；Secret matrix/s3-backup、kube-system/chentu-backup-age；S3_ENDPOINT / S3_BUCKET 可覆盖。
set -euo pipefail
umask 077
u=matrix; ns=${MATRIX_NAMESPACE:-matrix}; pod=matrix-tuwunel-0
command -v s5cmd >/dev/null || { echo "missing: s5cmd（operator tool, install explicitly）" >&2; exit 2; }
command -v age >/dev/null || { echo "missing: age; refusing unencrypted backup" >&2; exit 2; }
: "${AGE_RECIPIENT:=$(kubectl -n kube-system get secret chentu-backup-age -o jsonpath='{.data.recipient}' 2>/dev/null | base64 -d || true)}"
[ -n "$AGE_RECIPIENT" ] || { echo "missing: AGE_RECIPIENT or kube-system/chentu-backup-age; refusing unencrypted backup" >&2; exit 2; }
AWS_ACCESS_KEY_ID=$(kubectl -n $ns get secret s3-backup -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' 2>/dev/null | base64 -d) || true
AWS_SECRET_ACCESS_KEY=$(kubectl -n $ns get secret s3-backup -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' 2>/dev/null | base64 -d) || true
[ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] || { echo "missing: Secret $ns/s3-backup" >&2; exit 2; }
S3_ENDPOINT=${S3_ENDPOINT:-$(kubectl -n $ns get secret s3-backup -o jsonpath='{.data.AWS_ENDPOINTS}' 2>/dev/null | base64 -d)}
[ -n "$S3_ENDPOINT" ] || { echo "missing: S3_ENDPOINT (or AWS_ENDPOINTS in Secret s3-backup)" >&2; exit 2; }
case "$S3_ENDPOINT" in
  https://*)
    [ -f /usr/local/share/ca-certificates/chentu-ca.crt ] \
      || { echo "missing: 内部 CA 未安装（explicitly install the site CA on this operator）" >&2; exit 2; } ;;
esac
kubectl -n $ns get pod $pod >/dev/null 2>&1 || { echo "fail $u pod $pod not found (is the matrix release installed?)" >&2; exit 1; }

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION=us-east-1
S3_BUCKET=${S3_BUCKET:-chentu-backup}; DATE=$(date +%Y%m%d-%H%M%S)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
sidecar() { kubectl -n $ns exec $pod -c backup -- sh -c "$1"; }

# 1) 触发在线备份，等新的 backup id 出现（RocksDB 备份仓库的 meta/<id>）
latest='ls /var/lib/tuwunel/backups/meta 2>/dev/null | sort -n | tail -1'
before=$(sidecar "$latest")
sidecar 'kill -USR2 "$(pidof tuwunel)"' || { echo "fail $u could not signal tuwunel from the backup sidecar" >&2; exit 1; }
after="$before"
for _ in $(seq 1 120); do
  after=$(sidecar "$latest")
  [ -n "$after" ] && [ "$after" != "$before" ] && break
  sleep 5
done
[ -n "$after" ] && [ "$after" != "$before" ] \
  || { echo "fail $u no new RocksDB backup after SIGUSR2 (kubectl -n $ns logs $pod -c tuwunel | grep -i backup)" >&2; exit 1; }

# 2) 备份仓库 + 媒体打包；tar 的退出码经 kubectl exec 传回（媒体目录首次上传前不存在）
f="$tmp/tuwunel-$DATE.tar"
sidecar 'cd /var/lib/tuwunel && if [ -d db/media ]; then tar -cf - backups db/media; else tar -cf - backups; fi' > "$f" \
  || { echo "fail $u tar of backups/media failed" >&2; exit 1; }
[ -s "$f" ] || { echo "fail $u empty archive" >&2; exit 1; }
tar -tf "$f" >/dev/null || { echo "fail $u archive corrupt (exec stream interrupted?)" >&2; exit 1; }
age -r "$AGE_RECIPIENT" -o "$f.age" "$f" || { echo "fail $u encryption failed" >&2; exit 1; }
rm -f "$f"
f="$f.age"
dst="s3://$S3_BUCKET/matrix/$DATE/$(basename "$f")"
s5cmd --endpoint-url "$S3_ENDPOINT" cp "$f" "$dst" || { echo "fail $u upload to $dst failed"; exit 1; }
echo "ok $u backup_id=$after $(du -h "$f" | cut -f1) -> $dst"
