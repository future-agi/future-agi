"""Native check: the REAL inspect_daemon_storage on a rootful classic overlay2 daemon.

Runs only on a disposable GitHub-hosted Linux runner, as the non-root runner
user, which cannot read /var/lib/docker. Setup and admission depend on this
measurement; a failure here would otherwise surface only after a full product
qualification as a setup refusal. On failure it reruns the same fixed probe in
the same container shape and prints which probe line refused and Docker's own
error. Nothing here is qualification evidence.

    python -I deploy/tests/update_e2e/native_storage_probe.py IMAGE@DIGEST
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

CONTEXT = 'colima-fagi-update'
DOCKER = ['docker', '--context', CONTEXT]

WRAPPER = r'''
import sys, traceback
source = sys.stdin.read()
namespace = {"__name__": "probe"}
exec(compile(source, "daemon_overlay2.py", "exec"), namespace)
try:
    print("observed", namespace["observe"]())
except Exception as error:
    frames = [f for f in traceback.extract_tb(error.__traceback__)
              if f.filename == "daemon_overlay2.py" and f.name != "require"]
    print("refused", type(error).__name__, [(f.lineno, f.name, f.line) for f in frames])
'''


class Host(SimpleNamespace):
    """The Installation surface inspect_daemon_storage uses, routed by --context."""
    def subprocess_env(self):
        return dict(self.environ)

    def docker_run(self, *args, check=True):
        return self.runner([*self.docker, *args], cwd=str(self.root), env=self.subprocess_env(),
                           text=True, capture_output=True)


def docker(*args, **kwargs):
    return subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=1800, **kwargs)


def diagnose(image_id):
    from deploy.update.docker_host import DAEMON_STORAGE_HELPERS, fixed_source
    probe, binds, _ = DAEMON_STORAGE_HELPERS['helper-overlay2']
    args = ['run', '--rm', '-i', '--pull', 'never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--user', '0:0']
    for src, dest, options in binds:
        args += ['--mount', 'type=bind,src=' + src + ',dst=' + dest + options]
    args += ['--entrypoint', '/usr/local/bin/python3', image_id, '-I', '-B', '-S', '-c', WRAPPER]
    result = docker(*args, input=fixed_source(probe).decode())
    print('diagnostic exit', result.returncode)
    print('diagnostic stdout', result.stdout.strip()[:4000])
    print('diagnostic stderr', result.stderr.strip()[:4000])


def main(image):
    if not (sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'):
        raise SystemExit('native GitHub-hosted Linux runner required')
    if os.geteuid() == 0:
        raise SystemExit('run as the non-root runner user')
    from deploy.update.docker_host import inspect_daemon_storage
    info = json.loads(docker('info', '--format', '{{json .}}', check=True).stdout)
    print('daemon', {key: info.get(key) for key in ('ServerVersion', 'KernelVersion', 'Driver', 'DriverStatus',
                                                     'DockerRootDir', 'SecurityOptions', 'CgroupVersion')})
    docker('pull', '--platform', 'linux/amd64', image, check=True)
    image_id = json.loads(docker('image', 'inspect', image, check=True).stdout)[0]['Id']
    environ = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
    with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
        host = Host(root=Path(temporary), docker=list(DOCKER), environ=environ, env={}, env_overlay={}, runner=None)
        registration = SimpleNamespace(binding=SimpleNamespace(docker_context=CONTEXT, daemon_identity=info['ID']))
        observation = inspect_daemon_storage(host, registration, helper_image_id=image_id, timeout=90)
    print('observation', observation.status, observation.backend, observation.available_bytes,
          [(row.role, row.device, row.filesystem) for row in (observation.stores)])
    if observation.status != 'available' or observation.backend != 'helper-overlay2':
        diagnose(image_id)
        raise SystemExit('daemon-side storage measurement unavailable')
    # /var/lib is readable by this user; on the runner it shares the root filesystem.
    local = os.stat('/var/lib')
    device = '{}:{}'.format(os.major(local.st_dev), os.minor(local.st_dev))
    values = os.statvfs('/var/lib')
    expected = values.f_bavail * values.f_frsize
    print('host /var/lib', device, expected)
    if observation.stores[0].device == device and abs(observation.available_bytes - expected) > 2 * 1024 ** 3:
        raise SystemExit('helper measurement disagrees with the host filesystem')
    print('daemon-side overlay2 capacity measured')


if __name__ == '__main__':
    main(*sys.argv[1:])
