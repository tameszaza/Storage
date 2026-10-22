#!/usr/bin/python3
"""Mount only the service filesystem image on the known external volume."""
import json,os,subprocess,sys
from pathlib import Path
VOLUME=Path('/run/media/tameszaza/New Volume');IMAGE=VOLUME/'Cyborg Services/services.ext4';ROOT=Path('/srv/cyborg-services')
def main():
 if os.geteuid()!=0:raise SystemExit('Root required')
 subprocess.run(['/usr/bin/findmnt','-rn','-M',str(VOLUME),'-S','UUID=A87A-15B6'],check=True,stdout=subprocess.DEVNULL)
 if IMAGE.is_symlink() or not IMAGE.is_file():raise SystemExit('Service disk is missing')
 ROOT.mkdir(mode=0o755,exist_ok=True)
 if not ROOT.is_mount():subprocess.run(['/usr/bin/mount','-t','ext4','-o','loop,noatime,nodev,nosuid',str(IMAGE),str(ROOT)],check=True)
 source=subprocess.check_output(['/usr/bin/findmnt','-rn','-M',str(ROOT),'-o','SOURCE'],text=True).strip()
 info=json.loads(subprocess.check_output(['/usr/sbin/losetup','--json','--output','BACK-FILE',source],text=True))
 if Path(info['loopdevices'][0]['back-file']).resolve()!=IMAGE.resolve():raise SystemExit('Unexpected backing disk')
 if subprocess.check_output(['/usr/bin/findmnt','-rn','-M',str(ROOT),'-o','FSTYPE'],text=True).strip()!='ext4':raise SystemExit('Unexpected service filesystem')
if __name__=='__main__':main()
