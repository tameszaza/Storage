#!/usr/bin/python3
import json,pathlib,subprocess
base=pathlib.Path('/srv/docker/tamestorage/deploy/digital')
credentials=pathlib.Path('/home/tameszaza/.config/uptime-kuma/admin.json').read_text()
result=subprocess.run(['docker','exec','-i','-w','/app','uptime-kuma','node','-e',(base/'kuma-digital.js').read_text()],input=credentials,text=True,capture_output=True)
if result.returncode:
    print('Kuma provisioning failed; credentials were not logged.');raise SystemExit(1)
report=json.loads(result.stdout)
inventory=pathlib.Path('/srv/docker/uptime-kuma/monitors.json')
items=json.loads(inventory.read_text())
if not any(x['name']==report['name'] for x in items):
    items.append({'name':report['name'],'group':'Cyborg','type':'http','url':'http://127.0.0.1:9780/cyborg/digital','interval':60,'retryInterval':30,'timeout':12})
    inventory.write_text(json.dumps(items,indent=2)+'\n')
mapping=pathlib.Path('/srv/docker/uptime-kuma/monitor-map.json')
data=json.loads(mapping.read_text())
if not any(x['name']==report['name'] for x in data['monitors']):
    data['monitors'].append({'id':report['id'],'name':report['name'],'group':'Cyborg','active':True})
    mapping.write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(report))
