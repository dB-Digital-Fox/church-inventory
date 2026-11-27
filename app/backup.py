"""Backups: a consistent SQLite snapshot plus all uploaded photos, as one .tar.gz."""
import glob
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime


def create_backup(db_path, upload_dir, backup_dir, keep=14):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"church-inventory-{stamp}.tar.gz")
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = os.path.join(tmp, "church.db")
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        partial = target + ".part"
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(snapshot, arcname="church.db")
            if os.path.isdir(upload_dir):
                tar.add(upload_dir, arcname="uploads")
        os.replace(partial, target)
    prune(backup_dir, keep)
    return target


def list_backups(backup_dir):
    files = sorted(glob.glob(os.path.join(backup_dir, "church-inventory-*.tar.gz")), reverse=True)
    return [{"name": os.path.basename(f), "size": os.path.getsize(f),
             "mtime": datetime.fromtimestamp(os.path.getmtime(f))} for f in files]


def prune(backup_dir, keep):
    files = sorted(glob.glob(os.path.join(backup_dir, "church-inventory-*.tar.gz")), reverse=True)
    for old in files[max(keep, 1):]:
        try:
            os.remove(old)
        except OSError:
            pass
