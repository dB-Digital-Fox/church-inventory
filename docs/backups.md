# Backups and restore

## What is backed up

Every night after `BACKUP_HOUR` (default 02:00) the app writes
`church-inventory-YYYYMMDD-HHMMSS.tar.gz` to the backup folder. It contains a consistent copy of
the database (`church.db`, made with SQLite's online backup, so it's safe while the app runs) and
all photos (`uploads/`). The newest `BACKUP_KEEP` (default 14) are kept. Each backup is also
recorded in the audit log.

Admins can create a backup any time and download it under **Administration → Backups**.

## Put backups on a USB drive

```bash
lsblk                                   # find the drive, e.g. /dev/sda1
sudo mkdir -p /mnt/usb
echo '/dev/sda1 /mnt/usb ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount -a
sudo mkdir -p /mnt/usb/church-backups && sudo chown 1000:1000 /mnt/usb/church-backups
```

In `.env` set `BACKUP_PATH=/mnt/usb/church-backups`, then `docker compose up -d`.
(Use the drive's `UUID=` from `blkid` in fstab instead of `/dev/sda1` if you have more than one disk.)

## Off-site copy (recommended)

A USB drive next to the Pi doesn't help after a fire or theft. `rclone` can copy the backups,
encrypted, to Google Drive, OneDrive, Backblaze B2 and others:

```bash
sudo apt install rclone
rclone config            # add a remote, e.g. "gdrive", then a "crypt" remote "safe" on top of it
crontab -e
# every day at 03:30:
30 3 * * * rclone copy /mnt/usb/church-backups safe:church-backups --max-age 48h
```

## Restore

```bash
docker compose down
mkdir restore && tar -xzf /mnt/usb/church-backups/church-inventory-20261127-020000.tar.gz -C restore
mv data data.old                         # keep the current state, just in case
mkdir data && cp restore/church.db data/ && cp -r restore/uploads data/
sudo chown -R 1000:1000 data
docker compose up -d
docker compose exec app flask --app app verify-audit
```

Test a restore once on another computer, so you know it works before you need it.
