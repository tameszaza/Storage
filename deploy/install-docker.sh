#!/usr/bin/env bash
set -euo pipefail

deploy_root=/home/tameszaza/nas-deploy
compose_root=/srv/docker/tamestorage

test "$(id -u)" -eq 0
test -f "$deploy_root/compose.yaml"
test -f "$deploy_root/Dockerfile"
mountpoint -q /srv/tamestorage
mountpoint -q /srv/tamestorage-backup

install -d -o tameszaza -g tameszaza -m 0755 "$compose_root"
rsync -rltD --delete \
    --exclude=deploy/ \
    --exclude=.git/ \
    --exclude=.venv/ \
    --exclude=__pycache__/ \
    "$deploy_root/" "$compose_root/"
chown -R tameszaza:tameszaza "$compose_root"

cd "$compose_root"
sudo -u tameszaza docker compose config --quiet
sudo -u tameszaza docker compose build --pull
sudo -u tameszaza docker compose up -d

for _ in $(seq 1 30); do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' tamestorage 2>/dev/null || true)"
    if [[ "$health" == healthy ]]; then
        break
    fi
    sleep 2
done

[[ "$(docker inspect --format '{{.State.Health.Status}}' tamestorage)" == healthy ]]

systemctl disable --now tamestorage.service 2>/dev/null || true
rm -f /etc/systemd/system/tamestorage.service
systemctl daemon-reload

systemctl restart caddy.service
systemctl enable --now tamestorage-backup.timer

echo "DOCKER_INSTALL_COMPLETE"
docker compose ps
