"""Completed recording retention; called only by the existing temp cleanup worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time


def signature(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def cleanup(root,inputs,outputs,*,now=None,age=86400):
    root,inputs,outputs=map(Path,(root,inputs,outputs));now=time.time() if now is None else now
    if any(p.is_symlink() or not p.is_dir() for p in (root,inputs,outputs)):
        raise ValueError('Recording storage is unavailable')
    if (root/'READY').read_text().strip()!='tamestorage-transcription-v1':
        raise ValueError('Recording storage is unavailable')
    database=root/'queue.sqlite'
    if database.is_symlink() or not database.is_file():raise ValueError('Recording queue is unavailable')
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=3)
    db.row_factory=sqlite3.Row
    removed=0
    try:
        rows=db.execute("SELECT id,filename,size,sha256,updated,result_dir,options FROM jobs WHERE state='completed' AND updated<? AND result_dir IS NOT NULL",(now-age,)).fetchall()
        with_dir=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        inputs_fd=os.open(inputs,with_dir)
        try:
            for row in rows:
                job_fd=source_fd=None
                try:
                    if not re.fullmatch('[0-9a-f]{32}',row['id']):continue
                    if json.loads(row['options']).get('kind')=='lyrics':continue
                    relative=row['result_dir']
                    if not relative or Path(relative).name!=relative or relative in {'.','..'}:continue
                    result=outputs/relative
                    if result.is_symlink() or not result.is_dir():continue
                    if not all((result/f).is_file() and not (result/f).is_symlink() for f in ('Transcript.txt','Review.txt')):continue
                    job_fd=os.open(row['id'],with_dir,dir_fd=inputs_fd)
                    filename='Recording'+Path(row['filename']).suffix.lower()
                    source_fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=job_fd)
                    before=os.fstat(source_fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_size!=row['size'] or max(before.st_ctime,row['updated'])>=now-age:continue
                    digest=hashlib.sha256()
                    while True:
                        block=os.read(source_fd,1024*1024)
                        if not block:break
                        digest.update(block)
                    after=os.fstat(source_fd)
                    current=os.stat(filename,dir_fd=job_fd,follow_symlinks=False)
                    if digest.hexdigest()!=row['sha256'] or signature(before)!=signature(after) or signature(before)!=signature(current):continue
                    os.unlink(filename,dir_fd=job_fd);removed+=1
                    try:os.rmdir(row['id'],dir_fd=inputs_fd)
                    except OSError:pass # Preserve any unrelated files in this job folder.
                except (OSError,ValueError,TypeError,AttributeError):
                    continue # Preserve unknown/changed inputs; never infer ownership by age alone.
                finally:
                    if source_fd is not None:os.close(source_fd)
                    if job_fd is not None:os.close(job_fd)
        finally:os.close(inputs_fd)
    finally:db.close()
    return removed


if __name__=='__main__':
    try:
        count=cleanup('/srv/tamestorage/.tamestorage_state/transcription',
            '/srv/tamestorage/uploads/temp/Transcription','/srv/tamestorage/uploads/Admin/Transcripts',
            age=int(os.environ.get('TAMES_RECORDING_RETENTION_SECONDS','86400')))
        print(f'Completed recording cleanup: {count} audio file(s) removed')
    except (OSError,ValueError,sqlite3.Error):
        print('Recording cleanup deferred: storage or queue unavailable')
