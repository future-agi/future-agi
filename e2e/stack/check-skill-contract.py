"""Authenticate the regenerated SDK snapshot against the product checkout before packaging."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

SKILLS = '/model-hub/skills/'
HTTP_METHODS = {'get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace'}
CONTRACTS = ('swagger.json', 'mcp-tools.json', 'SOURCE.json')
# Exact generator-owned paths, not entire directories: per-resource subclasses and
# catalog entries are hand-written. A new scaffold must be committed in the SDK first.
GENERATED = (
    {f'sdkgen/contracts/{name}' for name in CONTRACTS}
    | {f'python/fi/platform/{name}' for name in (
        '_wire_definitions.json', 'models.py', '_operations.py', 'resources/base.py', 'resources/__init__.py',
    )}
    | {f'typescript/futureagi/src/platform/{name}' for name in (
        '_wireDefinitions.ts', 'models.ts', '_operations.ts', 'resources/base.ts', 'resources/index.ts',
    )}
)


def check_changes(sdk):
    def paths(*args):
        return set(subprocess.check_output(['git', '-C', sdk, *args], text=True).split('\0')) - {''}

    # Include staged, unstaged and untracked files (even ignored files). Generator
    # commands run with -B so their own bytecode is not a checkout change.
    changed = paths('diff', '--no-renames', '--name-only', '-z', 'HEAD') | paths('ls-files', '--others', '-z')
    unexpected = changed - GENERATED
    if unexpected:
        raise ValueError(f'SDK regeneration touched files outside the generated outputs: {sorted(unexpected)}')


def projection(api):
    paths = {path: item for path, item in api['paths'].items() if path.startswith(SKILLS)}
    definitions = {}

    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                prefix = '#/definitions/'
                ref = value['$ref']
                if not ref.startswith(prefix):
                    raise ValueError('Unsupported reference in Skills contract')
                name = ref[len(prefix):]
                if name not in definitions:
                    definitions[name] = api['definitions'][name]
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(paths)
    return paths, definitions


def operations(paths):
    return {(method.upper(), path) for path, item in paths.items() for method in HTTP_METHODS & item.keys()}


def check_slice(api, swagger, catalog, sdk_catalog):
    """Every Skills operation and MCP tool the SDK ships must exist, unchanged, in the tested backend.

    The SDK wraps a reviewed subset of the backend's Skills API. A backend operation the SDK
    does not wrap yet is a coverage gap, reported in the receipt, not a break; an operation the
    SDK wraps that the backend removed, renamed or reshaped fails the build.
    """
    backend_paths, _ = projection(api)
    sdk_paths, _ = projection(swagger)
    backend_ops = operations(backend_paths)

    shipped, tools = set(), {}
    for entry in sdk_catalog['operations']:
        op = entry['operation']
        if not op['path'].startswith(SKILLS):
            continue
        endpoint = (op['method'].upper(), op['path'])
        surfaces = entry['surfaces']
        if 'sdk' in surfaces and surfaces['sdk'] is not False:
            shipped.add(endpoint)
        if 'mcp' in surfaces and surfaces['mcp'] is not False:
            name = (surfaces['mcp'] or {}).get('name') or entry['id'].replace('.', '_')
            tools[name] = endpoint
    if not shipped:
        raise ValueError('Resolved SDK ships no Skills operations')
    missing = sorted(shipped - backend_ops)
    if missing:
        raise ValueError(f'SDK ships Skills operations the tested backend does not expose: {missing}')
    for method, path in shipped:
        mine = (sdk_paths.get(path) or {}).get(method.lower())
        theirs = backend_paths[path][method.lower()]
        if mine != theirs:
            raise ValueError(f'Regenerated SDK operation differs from the tested backend: {method} {path}')
    # Every definition a shipped operation reaches must match the backend's.
    def reached(api_doc, item_paths):
        _, models = projection({'paths': item_paths, 'definitions': api_doc['definitions']})
        return models
    shipped_items = {path: {m.lower(): backend_paths[path][m.lower()] for m, p in shipped if p == path} for _, path in shipped}
    if reached(api, shipped_items) != reached(swagger, shipped_items):
        raise ValueError('Regenerated SDK models differ from the tested backend for shipped Skills operations')

    backend_tools = {t['name']: (t['request']['method'].upper(), t['request']['path'])
                     for t in catalog['tools'] if t['request']['path'].startswith(SKILLS)}
    unknown = sorted(name for name, binding in tools.items() if backend_tools.get(name) != binding)
    if unknown:
        raise ValueError(f'SDK Skills MCP tools are absent or bound differently in the tested backend: {unknown}')
    return {
        'skills_operations': {'backend': len(backend_ops), 'sdk': len(shipped),
                              'not_wrapped': sorted(f'{m} {p}' for m, p in backend_ops - shipped)},
        'skills_mcp_tools': {'backend': len(backend_tools), 'sdk': len(tools),
                             'not_wrapped': sorted(set(backend_tools) - set(tools))},
    }


def check(root, sdk, baseline):
    check_changes(sdk)
    product = subprocess.check_output(['git', '-C', root, 'rev-parse', 'HEAD'], text=True).strip()
    raw_api = (root / 'api_contracts/openapi/swagger.json').read_bytes()
    raw_tools = (root / 'futureagi/mcp_server/catalog/tools.generated.json').read_bytes()
    api_hash, mcp_hash = (hashlib.sha256(data).hexdigest() for data in (raw_api, raw_tools))
    catalog = json.loads(raw_tools)
    files = {name: (sdk / 'sdkgen/contracts' / name).read_bytes() for name in CONTRACTS}
    swagger, tools, source = (json.loads(files[name]) for name in CONTRACTS)
    if source['backend_commit'] != product or source['non_skills_sdk_commit'] != baseline:
        raise ValueError('Regenerated SOURCE.json does not identify the tested product and SDK baseline')
    if (api_hash != source['openapi_sha256'] or api_hash != swagger['x-source-sha256']
            or api_hash != catalog['source_contract_sha256'] or mcp_hash != source['mcp_sha256']
            or mcp_hash != tools['x-source-sha256']
            or source['non_skills_slice_sha256'] != swagger['x-skills-only-baseline-sha256']):
        raise ValueError('Regenerated snapshot source hash mismatch')
    if tools['tools'] != [{'name': name} for name in sorted(t['name'] for t in catalog['tools'])]:
        raise ValueError('SDK tool inventory does not match the tested catalog')
    sdk_catalog = yaml.safe_load((sdk / 'sdkgen/catalog/operations.yaml').read_bytes())
    coverage = check_slice(json.loads(raw_api), swagger, catalog, sdk_catalog)
    return {'source': source, 'coverage': coverage,
            'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}


if __name__ == '__main__':
    print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
