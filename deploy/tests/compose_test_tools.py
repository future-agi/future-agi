"""Daemon-free deployment rendering with the installer's supported resolver.

CI's Ubuntu runner supplies the Docker Compose plugin. Developer hosts may
supply the identical Compose binary on PATH instead; never install a global
plugin, silently omit rendering, or start a Docker resource for these tests.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def compose_command():
    from deploy.update.install import resolve_compose_command
    def context(command):
        name = Path(command[0]).name
        selected = os.environ.get('DOCKER_CONTEXT')
        return [command[0], '--context', selected, *command[1:]] if selected and name == 'docker' else list(command)
    def probe(command, **kwargs):
        return subprocess.run(context(command), timeout=30, **kwargs)
    return context(resolve_compose_command(runner=probe))
