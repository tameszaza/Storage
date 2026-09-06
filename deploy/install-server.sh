#!/usr/bin/env bash
set -euo pipefail

deploy_root=/home/tameszaza/nas-deploy
primary_mount=/srv/tamestorage
backup_mount=/srv/tamestorage-backup
primary_uuid=1e5557c2-1302-4632-9937-3e2db211c295
backup_uuid=70e5d00b-c0e1-4bdd-84cc-1af5e56c75f8

test "$(id -u)" -eq 0
test -d "$deploy_root"

install -d -m 0755 "$primary_mount" "$backup_mount"

if [[ ! -e /etc/fstab.pre-tamestorage ]]; then
    cp -a /etc/fstab /etc/fstab.pre-tamestorage
fi

awk -v primary="$primary_uuid" -v backup="$backup_uuid" \
    'index($0, primary) == 0 && index($0, backup) == 0 { print }' \
    /etc/fstab > /etc/fstab.tamestorage-new
printf '\n# Tamestorage USB disks (installed by NAS setup)\n' >> /etc/fstab.tamestorage-new
printf 'UUID=%s /srv/tamestorage ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2\n' "$primary_uuid" >> /etc/fstab.tamestorage-new
printf 'UUID=%s /srv/tamestorage-backup ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2\n' "$backup_uuid" >> /etc/fstab.tamestorage-new
install -o root -g root -m 0644 /etc/fstab.tamestorage-new /etc/fstab
rm -f /etc/fstab.tamestorage-new

systemctl daemon-reload
mount "$primary_mount" 2>/dev/null || true
mount "$backup_mount" 2>/dev/null || true

[[ "$(findmnt -n -o UUID --target "$primary_mount")" == "$primary_uuid" ]]
[[ "$(findmnt -n -o UUID --target "$backup_mount")" == "$backup_uuid" ]]

install -d -o tameszaza -g tameszaza -m 0770 \
    "$primary_mount/uploads" \
    "$primary_mount/.tamestorage_state"
install -d -o tameszaza -g tameszaza -m 0775 /srv/music

secret_key="$(openssl rand -hex 32)"
sed "s/GENERATED_DURING_INSTALL/$secret_key/" "$deploy_root/deploy/tamestorage.env" > /etc/tamestorage.env
chown root:docker /etc/tamestorage.env
chmod 0640 /etc/tamestorage.env

install -o root -g root -m 0755 "$deploy_root/deploy/tamestorage-backup" /usr/local/sbin/tamestorage-backup
install -o root -g root -m 0644 "$deploy_root/deploy/tamestorage-backup.service" /etc/systemd/system/tamestorage-backup.service
install -o root -g root -m 0644 "$deploy_root/deploy/tamestorage-backup.timer" /etc/systemd/system/tamestorage-backup.timer

caddy validate --config "$deploy_root/deploy/Caddyfile"
if [[ ! -e /etc/caddy/Caddyfile.pre-tamestorage ]]; then
    cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-tamestorage
fi
install -o root -g root -m 0644 "$deploy_root/deploy/Caddyfile" /etc/caddy/Caddyfile

testparm -s "$deploy_root/deploy/smb.conf" >/dev/null
if [[ ! -e /etc/samba/smb.conf.pre-tamestorage ]]; then
    cp -a /etc/samba/smb.conf /etc/samba/smb.conf.pre-tamestorage
fi
install -o root -g root -m 0644 "$deploy_root/deploy/smb.conf" /etc/samba/smb.conf

systemctl daemon-reload
systemctl disable --now nmbd.service samba-ad-dc.service 2>/dev/null || true
systemctl enable --now tamestorage-backup.timer smbd.service
systemctl restart smbd.service
systemctl restart caddy.service

# Samba cannot bind directly to Tailscale's non-broadcast TUN interface.
# Keep smbd on loopback and let Tailscale expose raw SMB only to the tailnet.
tailscale serve --bg --tcp=445 tcp://127.0.0.1:445

if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow in on tailscale0 to any port 80 proto tcp comment 'Tamestorage web via Tailscale'
    ufw allow in on tailscale0 to any port 445 proto tcp comment 'Tamestorage SMB via Tailscale'
fi

echo "INSTALL_COMPLETE"
findmnt "$primary_mount" "$backup_mount"
df -hT "$primary_mount" "$backup_mount"
