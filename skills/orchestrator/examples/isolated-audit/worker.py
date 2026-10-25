"""A real offline audit: inspect the trusted snapshot, write a bounded finding."""
import datetime as dt
import json
from pathlib import Path

snapshot = json.loads(Path('/input/snapshot.json').read_bytes())
now = dt.datetime.now(dt.timezone.utc)
report = {'services': [], 'certificates': []}
for service in snapshot['services']:
    report['services'].append({'id': service['id'], 'active': service['active_state'] == 'active'})
for certificate in snapshot['certificates']:
    expiry = dt.datetime.fromisoformat(certificate['not_after'])
    report['certificates'].append({'id': certificate['id'], 'sha256': certificate['sha256'],
                                   'not_after': certificate['not_after'],
                                   'days_remaining': (expiry - now).days})
Path('/out/audit.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, sort_keys=True))
