"""Reuse the deployed image/dependencies with one small source layer."""
import os,shutil,subprocess,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
files=['lib/ai_client.py','lib/assistant_tools.py','lib/assistant_diary.py','routes/ai.py',
       'routes/obsidian_images.py','static/js/chat.js','templates/chat.html','deploy/obsidian-diary.mjs']
with tempfile.TemporaryDirectory(prefix='diary-build-',dir='/dev/shm') as temporary:
 target=Path(temporary)
 for name in files:
  p=target/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,p)
 subprocess.run(['docker','build','--build-arg','BASE_IMAGE='+os.environ.get('DIARY_BASE_IMAGE','tamestorage:latest'),
                 '-f',str(root/'deploy/Dockerfile.diary'),'-t','tamestorage:diary-candidate',str(target)],check=True)
