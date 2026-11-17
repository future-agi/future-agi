"""A fixture image must retain its application package after guard mounts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    image = root / 'image'
    app = image / 'app/backend'
    package = app / 'model_hub'
    package.mkdir(parents=True)
    (package / '__init__.py').touch()
    (package / 'application_marker.py').write_text('VALUE = "application"\n')
    tools = root / 'tools'
    reference = tools / 'reference/model_hub/tests'
    reference.mkdir(parents=True)
    (reference.parent / '__init__.py').touch()
    (reference / '__init__.py').touch()
    (reference / 'reference_marker.py').write_text('VALUE = "reference"\n')
    kit = root / 'kit'
    kit.mkdir()
    config = root / 'base.json'
    config.write_text(json.dumps({'services': {
        'backend': {'image': 'futureagi/future-agi:e2e-local'}}}))
    output = root / 'guarded.json'
    generator = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('compose-guards.py')
    subprocess.run([sys.executable, '-I', str(generator), str(config), str(kit), str(tools), str(output)], check=True)
    service = json.loads(output.read_text())['services']['backend']
    for mount in service['volumes']:
        target = image / mount['target'].lstrip('/')
        shutil.copytree(mount['source'], target, dirs_exist_ok=True)
    guarded_root = image / service['environment']['EVAL_GUARD_REFERENCE_ROOT'].lstrip('/')
    program = ('import sys; sys.path[:0] = sys.argv[1:]; '
               'from model_hub.application_marker import VALUE as application; '
               'from model_hub.tests.reference_marker import VALUE as reference; '
               'assert (application, reference) == ("application", "reference")')
    result = subprocess.run([sys.executable, '-I', '-c', program, str(guarded_root), str(app)],
                            capture_output=True, text=True)
    if result.returncode:
        # Only a fixed classification is printed, never process environment values.
        print(json.dumps({'status': 'FAIL', 'reason': 'guard mount hides the application package'}))
        raise SystemExit(1)
    print(json.dumps({'status': 'PASS', 'applicationAndReferenceImports': True}))
