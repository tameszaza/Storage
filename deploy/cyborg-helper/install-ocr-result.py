#!/usr/bin/env python3
"""Install only the OCR successful-run counter reset; no queue/cache changes."""
from pathlib import Path
import os,subprocess
if os.geteuid()!=0:raise SystemExit('Root access is required to update the system service')
expected='# Successful queue drains/standby must not consume the failure allowance.\n[Service]\nExecStopPost=+/bin/sh -c \'if [ "$${SERVICE_RESULT}" = success ]; then /usr/bin/systemctl reset-failed lecture-search-ocr.service; fi\'\n'
source=Path('/home/tameszaza/.local/lib/lecture-search/lecture-search-ocr-result.conf')
if source.read_text()!=expected:raise SystemExit('Unexpected configuration; preserving installed service')
path=Path('/etc/systemd/system/lecture-search-ocr.service.d/40-success-counter.conf')
path.parent.mkdir(exist_ok=True)
path.write_text(expected);path.chmod(0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','reset-failed','lecture-search-ocr.service','lecture-search-ocr.path'],check=True)
subprocess.run(['systemctl','restart','lecture-search-ocr.path'],check=True)
print('Installed successful-run counter reset and rearmed OCR watcher. Existing failure limit and resource bounds retained.')
