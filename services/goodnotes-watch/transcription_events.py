"""Optional transcription routing for the existing filesystem event watcher."""
from pathlib import Path

INBOX=Path('/srv/tamestorage/uploads/temp/Transcription')
SIGNAL=Path('/srv/tamestorage/.tamestorage_state/transcription/audio-wake')
EXTENSIONS={'.wav','.mp3','.m4a','.aac','.flac','.ogg','.opus','.mp4','.mov','.mkv','.webm','.aiff','.wma'}

def wake():
    if SIGNAL.parent.is_dir() and (SIGNAL.parent/'READY').is_file():SIGNAL.touch()

def seed():
    if INBOX.is_dir() and not INBOX.is_symlink():
        if any(not p.name.startswith('.') and not p.is_symlink() and p.is_file() and p.suffix.lower() in EXTENSIONS for p in INBOX.iterdir()):wake()

def event(parent,path,mask):
    if parent!=INBOX:return False
    if not path.name.startswith('.') and path.suffix.lower() in EXTENSIONS and mask&(0x8|0x80):wake()
    return True
