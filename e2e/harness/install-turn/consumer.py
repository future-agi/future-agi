"""Run as an unprivileged disposable Linux user; read one consume credential via stdin."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert os.getuid() != 0 and not any(k in os.environ for k in ('FI_API_KEY', 'FI_SECRET_KEY'))
    data = json.load(sys.stdin)
    home = Path.home()
    project = Path(tempfile.mkdtemp(dir=home, prefix='skill-turn-'))
    kind = data['kind']
    env = {'HOME': str(home), 'PATH': f'{Path(sys.executable).parent}:/usr/bin:/bin',
           'FI_CONTEXT_CREDENTIAL': data['credential'], 'PYTHONDONTWRITEBYTECODE': '1'}
    # Execute the verbatim UI recipe, fail on the first failed command, and never log the diff.
    run = subprocess.run(['bash', '-eu'], cwd=project, env=env, input=data['commands'],
                         capture_output=True, text=True, timeout=90)
    if run.returncode:
        print(run.stderr.replace(data['credential'], '[redacted]')[-1200:], file=sys.stderr)
        raise RuntimeError(f'{kind} install exited {run.returncode}')
    for path in project.rglob('*'):
        if path.is_file():
            assert not path.is_symlink() and data['credential'].encode() not in path.read_bytes()
    # The guest and its HOME are disposable. Include hidden caches outside the project.
    for path in home.rglob('*'):
        if not path.is_symlink() and path.is_file():
            assert data['credential'].encode() not in path.read_bytes()
    lock_path = '.futureagi-skills.lock.json' if kind == 'skill' else '.futureagi-context.lock.json'
    lock = json.loads((project / lock_path).read_text())
    identity = lock['skills'][0] if kind == 'skill' else lock
    assert identity['version_id'] == data['versionId'] and identity['digest'] == data['digest']
    root = project / ('.agents/skills' if data['profile'] == 'codex' else '.claude/skills') / data['skill']
    assert root.is_dir()
    installed = {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}
    assert installed == data['skillFiles']
    if kind == 'agent_context':
        assert {f['path']: f['sha256'] for f in lock['files']} == data['manifest']
        for entry in lock['materialization']['files']:
            assert sha(project / entry['destination']) == entry['sha256']
        assert (project / 'AGENTS.md').is_file()
    # The plan contains fixture bytes. Don't let a coding agent solve the question by reading it.
    (project / ('skill-plan.json' if kind == 'skill' else 'context-plan.json')).unlink()
    print(json.dumps({'project': str(project), 'kind': kind, 'profile': data['profile'],
                      'versionId': identity['version_id'], 'digest': identity['digest'], 'files': installed}))


if __name__ == '__main__':
    main()
