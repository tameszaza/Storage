#!/usr/bin/env python3
"""Manual install/update build: share one dependency tree; never run during publishing."""
from pathlib import Path
import json,subprocess,shutil,sys
base=Path(__file__).resolve().parent
q=base/"quartz"
node=Path.home()/".local/lib/goodnotes-links/node_modules/node/bin/node"
lock=json.loads((q/"quartz.lock.json").read_text())
# These upstream Git packages have no prepare hook or published dist files.
for name in ["remark-obsidian", "rehype-obsidian"]:
 p=q/".quartz/support"/name
 if not (p/"node_modules").exists():(p/"node_modules").symlink_to(q/"node_modules",target_is_directory=True)
 result=subprocess.run([str(node),str(q/"node_modules/tsup/dist/cli-default.js"),"--no-dts","--no-sourcemap"],cwd=p,capture_output=True,text=True,timeout=240)
 if result.returncode:print((result.stdout+result.stderr)[-6000:],flush=True);raise SystemExit(result.returncode)
 shutil.copytree(p/"dist",q/"node_modules/@quartz-community"/name/"dist",dirs_exist_ok=True)
 print(name+": built",flush=True)
selected=next((a.split("=",1)[1].split(",") for a in sys.argv if a.startswith("--only=")),None)
for name in lock["plugins"]:
 if selected and name not in selected:continue
 p=q/".quartz/plugins"/name
 if "--missing-only" in sys.argv and (p/"dist/index.js").is_file():continue
 deps=p/"node_modules"
 if deps.exists() and not deps.is_symlink():shutil.rmtree(deps)
 if not deps.is_symlink():deps.symlink_to(q/"node_modules",target_is_directory=True)
 result=subprocess.run([str(node),str(q/"node_modules/tsup/dist/cli-default.js"),"--no-dts","--no-sourcemap"],cwd=p,capture_output=True,text=True,timeout=240)
 print(name+": "+("built" if result.returncode==0 else "FAILED"),flush=True)
 if result.returncode:
  print((result.stdout+result.stderr)[-6000:],flush=True);raise SystemExit(result.returncode)
subprocess.run([str(node),"--import","tsx",str(q/"regenerate-index.mjs")],cwd=q,check=True)
