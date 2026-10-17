"""Merge an audited Storage inventory into the configured database; media stays in Telegram."""
import argparse
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from data.settings import DATABASE_PATH, BACKUP_DIR
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--database',type=Path,default=DATABASE_PATH)
    parser.add_argument('--admin',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    inventory=json.loads(args.inventory.read_text(encoding='utf-8-sig'))
    if not args.apply:
        print(json.dumps({'database':str(args.database),'videos':len(inventory['videos']),
                          'apply':False,'hint':'Stop the bot, then rerun with --apply'},ensure_ascii=False))
        return
    target=args.database.resolve()
    if target.exists():
        BACKUP_DIR.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=BACKUP_DIR/('before-storage-recovery-'+stamp+'.sqlite')
        with contextlib.closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as src:
            with contextlib.closing(sqlite3.connect(backup)) as dst:src.backup(dst)
        print('Database backed up:',backup)
    from database.connection import database
    from database.migrations import migrate
    from services.storage_recovery import restore
    database.open(target)
    try:
        migrate(BACKUP_DIR)
        result=restore(inventory,args.admin)
        assert database.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        print(json.dumps(result,ensure_ascii=False))
    finally:database.close()


if __name__=='__main__':main()
