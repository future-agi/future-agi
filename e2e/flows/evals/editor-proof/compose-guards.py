"""Create an additional proof-only compose file; never replace product harness bytes."""
import json
from pathlib import Path
import sys
import subprocess

config, kit, tools, output = map(Path, sys.argv[1:])
base = json.loads(config.read_text())
services = base['services']
local_names = ','.join(sorted(services))
result = {'services': {}}
manifest = {'python': [], 'node': [], 'goProxy': [], 'allowedComposeNames': sorted(services)}
offline_environment = {
    'NO_GCE_CHECK': 'True',
    'GCE_METADATA_HOST': '127.0.0.1:9',
    'GCE_METADATA_IP': '127.0.0.1:9',
    'AWS_EC2_METADATA_DISABLED': 'true',
    'CODE_EXECUTOR_URL': '',
    'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
}
for name, service in services.items():
    image = str(service.get('image', ''))
    environment = {'EVAL_GUARD_DIR': '/tmp/editor-guard', 'EVAL_GUARD_SERVICE': name,
                   'EVAL_GUARD_SERVICES': local_names}
    override = {}
    if image.startswith('futureagi/future-agi:'):
        environment.update(offline_environment)
        old = service.get('environment', {}).get('PYTHONPATH', '') or ''
        environment.update({'PYTHONPATH': '/proof/guard' + (':' + old if old else ''),
                            'EVAL_GUARD_REFERENCE_ROOT': '/app/backend',
                            'EVAL_GUARD_PYTHON_DEPS': '/proof/tools/python',
                            'FUTURE_AGI_TELEMETRY_DISABLED': 'true', 'AWS_EC2_METADATA_DISABLED': 'true',
                            'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
                            'FUTURE_AGI_LICENSE_URL': 'http://127.0.0.1:9',
                            'FUTURE_AGI_TELEMETRY_URL': 'http://127.0.0.1:9'})
        override['volumes'] = [
            {'type': 'bind', 'source': str(kit), 'target': '/proof/guard', 'read_only': True},
            {'type': 'bind', 'source': str(tools / 'reference/model_hub/tests'),
             'target': '/app/backend/model_hub/tests', 'read_only': True},
            {'type': 'bind', 'source': str(tools), 'target': '/proof/tools', 'read_only': True}]
        manifest['python'].append(name)
    elif name in ('mock-llm', 'mock-slack'):
        environment.update({'NODE_OPTIONS': '--require=/proof/guard/node-guard.cjs',
                            'EVAL_UNDICI_PATH': '/proof/tools/node_modules/undici'})
        override['volumes'] = [
            {'type': 'bind', 'source': str(kit), 'target': '/proof/guard', 'read_only': True},
            {'type': 'bind', 'source': str(tools), 'target': '/proof/tools', 'read_only': True}]
        manifest['node'].append(name)
    elif name in ('agentcc-gateway', 'fi-collector', 'fi-property-catalog-consumer',
                  'minio', 'peerdb-minio', 'temporal', 'peerdb-temporal', 'peerdb-flow-worker', 'peerdb-flow-snapshot-worker',
                  'peerdb-flow-api', 'peerdb-server'):
        proxy = 'http://editor-egress-recorder:3128'
        no_proxy = 'localhost,127.0.0.1,::1,0.0.0.0,::,' + local_names
        environment = {key: proxy for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                             'http_proxy', 'https_proxy', 'all_proxy')}
        environment.update({'NO_PROXY': no_proxy, 'no_proxy': no_proxy})
        override['depends_on'] = {'editor-egress-recorder': {'condition': 'service_started'}}
        if name in ('temporal', 'peerdb-temporal'):
            # The image's existing entrypoint is preserved after an own-host snapshot.
            original_entrypoint = json.loads(subprocess.check_output([
                'docker', 'image', 'inspect', '--format', '{{json .Config.Entrypoint}}', image]))
            original_command = service.get('command')
            if original_command is None:
                original_command = json.loads(subprocess.check_output([
                    'docker', 'image', 'inspect', '--format', '{{json .Config.Cmd}}', image]))
            assert isinstance(original_entrypoint, list) and original_entrypoint
            assert isinstance(original_command, list) and original_command
            override['entrypoint'] = ['/bin/sh', '/proof/go-own-host.sh', *original_entrypoint]
            # Compose clears image CMD when entrypoint is overridden; preserve auto-setup.
            override['command'] = original_command
            manifest.setdefault('preservedCommands', {})[name] = {
                'entrypoint': original_entrypoint, 'command': original_command}
            override['volumes'] = [{'type': 'bind', 'source': str(Path(__file__).with_name('go-own-host.sh')),
                                    'target': '/proof/go-own-host.sh', 'read_only': True}]
        manifest['goProxy'].append(name)
    else:
        continue
    override['environment'] = environment
    result['services'][name] = override
result['services']['editor-egress-recorder'] = {
    'image': 'futureagi/future-agi:e2e-local',
    'entrypoint': ['python', '/proof/guard/go-proxy.py'], 'command': [],
    'environment': {**offline_environment, 'EVAL_GUARD_DIR': '/tmp/editor-guard', 'PYTHONPATH': '/proof/guard',
                    'EVAL_GUARD_REFERENCE_ROOT': '/app/backend',
                    'EVAL_GUARD_PYTHON_DEPS': '/proof/tools/python'},
    'volumes': [
        {'type': 'bind', 'source': str(kit), 'target': '/proof/guard', 'read_only': True},
        {'type': 'bind', 'source': str(tools / 'reference/model_hub/tests'),
         'target': '/app/backend/model_hub/tests', 'read_only': True},
        {'type': 'bind', 'source': str(tools), 'target': '/proof/tools', 'read_only': True}],
    'restart': 'no', 'healthcheck': {'disable': True}}
manifest['recorder'] = ['editor-egress-recorder']
output.write_text(json.dumps(result, indent=2) + '\n')
output.with_name('guard-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
