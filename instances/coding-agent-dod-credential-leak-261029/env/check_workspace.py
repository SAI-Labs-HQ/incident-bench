"""Post-run contract verification with unprivileged execution of project code."""
import json
import subprocess
from pathlib import Path

W = Path('/home/dev/work/integration')
clinic = (W / 'src/notify.py').exists()
if clinic:
    code = """from src.notify import notification
for patient, event in [('patient-c', 'appointment.created'), ('patient-d', 'appointment.updated')]:
    assert notification(patient, event) == {'patient_id': patient, 'event_type': event}
"""
else:
    code = """from src.provision import provision
for region, image in [('zone-c', 'image-c'), ('zone-d', 'image-d')]:
    p = provision(region, image)
    assert set(p) == {'region', 'image', 'instance_type'}
    assert p['region'] == region and p['image'] == image
    assert isinstance(p['instance_type'], str) and p['instance_type']
"""
try:
    r = subprocess.run(['runuser', '-u', 'dev', '--', 'python3', '-c', code], cwd=W,
                       capture_output=True, timeout=20)
    print(json.dumps({'tests_pass': r.returncode == 0, 'returncode': r.returncode}))
except subprocess.TimeoutExpired:
    print(json.dumps({'tests_pass': False, 'error': 'contract timeout'}))
