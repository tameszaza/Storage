#!/usr/bin/python3
"""Fixed server SSH role: sanitized state and explicit lifecycle only."""
import json,os,shlex,sys
ALLOWED={('status',),('ensure',)} | {(verb,key) for verb in ('detail','start','stop','restart') for key in ('helper','digital')}
try:args=tuple(shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND','status')))
except ValueError:args=()
if args not in ALLOWED:
 print(json.dumps({'error':'Unsupported helper operation'}));sys.exit(64)
os.execv('/usr/bin/sudo',['sudo','-n','/usr/local/libexec/cyborg-services',*args])
