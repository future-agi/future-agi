"""Regenerate the SDK snapshot and compare the shipped Skills subset to committed product inputs."""

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

OPENAPI = 'api_contracts/openapi/swagger.json'
MCP = 'futureagi/mcp_server/catalog/tools.generated.json'
MCP_CATALOG = 'futureagi/mcp_server/catalog/tools.yaml'
MCP_GENERATOR = 'futureagi/mcp_server/tool_generation.py'
MCP_PACKAGE = 'futureagi/mcp_server'
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


def referenced(doc, ref):
    # Swagger 2 local JSON references; direct model cycles are common.
    if not ref.startswith(('#/definitions/', '#/parameters/', '#/responses/')):
        raise ValueError(f'Unsupported reference in shipped Skills contract: {ref}')
    value = doc
    for part in ref[2:].split('/'):
        value = value[part.replace('~1', '/').replace('~0', '~')]
    return value


def inline_reusable(doc, value, visiting=()):
    if isinstance(value, list):
        return [inline_reusable(doc, item, visiting) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get('$ref')
    if ref and not ref.startswith('#/definitions/'):
        if ref in visiting:
            raise ValueError(f'Circular reusable parameter or response: {ref}')
        return inline_reusable(doc, referenced(doc, ref), (*visiting, ref))
    return {key: inline_reusable(doc, item, visiting) for key, item in value.items()}


def effective_operation(doc, method, path):
    item = doc['paths'].get(path, {})
    if method.lower() not in item:
        return None
    op = dict(item[method.lower()])
    # A method overrides a path parameter with the same (in, name), regardless
    # of whether either parameter is inline or a reusable Parameter Object.
    parameters = {}
    for param in [*item.get('parameters', []), *op.pop('parameters', [])]:
        param = inline_reusable(doc, param)
        name = param['name'].lower() if param['in'] == 'header' else param['name']
        if param['in'] == 'header':
            param = {**param, 'name': name}  # HTTP header names are case-insensitive
        parameters[param['in'], name] = param
    return {**inline_reusable(doc, op), 'parameters': parameters}


def reached_models(doc, operation):
    models = {}

    def visit(value):
        if isinstance(value, dict):
            ref = value.get('$ref')
            if ref and ref not in models:
                models[ref] = inline_reusable(doc, referenced(doc, ref))
                visit(models[ref])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(operation)
    return models


def check_tool(name, endpoint, declared, tool):
    if tool is None or endpoint != (tool['request']['method'].upper(), tool['request']['path']):
        raise ValueError(f'SDK Skills MCP tool absent or bound differently in the tested backend: {name}')
    hints = tool.get('annotations', {})
    if 'access' in declared:
        access = declared['access']
        if (access not in {'read', 'write', 'destructive'}
                or hints.get('readOnlyHint') is not (access == 'read')
                or hints.get('destructiveHint') is not (access == 'destructive')):
            raise ValueError(f'SDK Skills MCP access differs for {name}')
    if 'open_world' in declared and hints.get('openWorldHint') is not declared['open_world']:
        raise ValueError(f'SDK Skills MCP open_world differs for {name}')
    for param, cap in declared.get('max_page_size', {}).items():
        schema = tool['inputSchema']['properties'].get(param, {})
        mapping = tool['request']['parameters']
        if (param not in [*mapping['query'], *mapping['body']] or schema.get('type') != 'integer'
                or type(schema.get('maximum')) is not int or type(cap) is not int or schema['maximum'] != cap):
            raise ValueError(f'SDK Skills MCP max_page_size differs for {name}.{param}')
    # Other explicit fields (including group or stability) must have an equal
    # backend value. Do not silently accept new, unmapped catalog settings.
    for key in declared.keys() - {'access', 'open_world', 'max_page_size'}:
        # The SDK defaults an omitted stability to 'stable' and the backend omits it.
        if key == 'stability' and declared[key] == 'stable' and tool.get(key, 'stable') == 'stable':
            continue
        if key not in tool or declared[key] != tool[key]:
            raise ValueError(f'SDK Skills MCP {key} differs for {name}')


def check_slice(api, swagger, catalog, sdk_catalog):
    """Compare the shipped Skills subset, reporting additional backend coverage separately."""
    backend_ops = {(method.upper(), path) for path, item in api['paths'].items() if path.startswith(SKILLS)
                   for method in HTTP_METHODS & item.keys()}
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
            declared = surfaces['mcp']
            name = declared.get('name') or entry['id'].replace('.', '_')
            if name in tools:
                raise ValueError(f'Duplicate SDK Skills MCP tool: {name}')
            tools[name] = (endpoint, declared)
    if not shipped:
        raise ValueError('Resolved SDK ships no Skills operations')
    missing = sorted(shipped - backend_ops)
    if missing:
        raise ValueError(f'SDK ships Skills operations the tested backend does not expose: {missing}')
    for method, path in sorted(shipped):
        mine = effective_operation(swagger, method, path)
        theirs = effective_operation(api, method, path)
        if mine != theirs:
            raise ValueError(f'Regenerated SDK operation differs from the tested backend: {method} {path}')
        if reached_models(swagger, mine) != reached_models(api, theirs):
            raise ValueError(f'Regenerated SDK models differ from the tested backend: {method} {path}')

    backend_tools = {t['name']: t for t in catalog['tools'] if t['request']['path'].startswith(SKILLS)}
    for name, (endpoint, declared) in tools.items():
        check_tool(name, endpoint, declared, backend_tools.get(name))
    return {
        'skills_operations': {'backend': len(backend_ops), 'sdk': len(shipped),
                              'not_wrapped': sorted(f'{m} {p}' for m, p in backend_ops - shipped)},
        'skills_mcp_tools': {'backend': len(backend_tools), 'sdk': len(tools),
                             'not_wrapped': sorted(set(backend_tools) - set(tools))},
    }


def blob(repo, commit, path):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected a full commit SHA')
    return subprocess.check_output(['git', '--no-replace-objects', '-C', repo, 'show', f'{commit}:{path}'])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run_sdk(sdk, *args):
    # stdout of this driver is reserved for the verified JSON receipt.
    subprocess.run([sys.executable, '-IB', *args], cwd=sdk, stdout=sys.stderr, check=True)


def check_catalog_provenance(root, product, raw_api, raw_tools):
    """The committed MCP manifest must be exactly what the committed generator renders
    from the committed OpenAPI file and tool catalog. This does not depend on which
    metadata fields the manifest carries (a contract hash or a tool-name fingerprint)."""
    if not isinstance(product, str) or not re.fullmatch(r'[0-9a-f]{40}', product):
        raise ValueError('Expected a full commit SHA')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # The generator is a module of the committed mcp_server package; import it
        # from that exact package rather than from the writable checkout.
        archive = subprocess.check_output(['git', '--no-replace-objects', '-C', root, 'archive', '--format=tar',
                                           product, MCP_PACKAGE])
        subprocess.run(['tar', '-x', '-C', tmp], input=archive, check=True)
        (tmp / 'swagger.json').write_bytes(raw_api)
        package_root = str(tmp / Path(MCP_PACKAGE).parent)
        saved = {name: module for name, module in sys.modules.items()
                 if name == 'mcp_server' or name.startswith('mcp_server.')}
        for name in saved:
            del sys.modules[name]
        sys.path.insert(0, package_root)
        try:
            module = importlib.import_module('mcp_server.tool_generation')
            manifest = module.generate_tool_manifest(tmp / 'swagger.json', tmp / MCP_CATALOG)
        finally:
            sys.path.remove(package_root)
            for name in [n for n in sys.modules if n == 'mcp_server' or n.startswith('mcp_server.')]:
                del sys.modules[name]
            sys.modules.update(saved)
    if (json.dumps(manifest, indent=2, sort_keys=False) + '\n').encode() != raw_tools:
        raise ValueError('Product MCP catalog was not generated from the committed OpenAPI file')


def regenerate(root, sdk, product):
    # Keep the expected bytes independent of the generator's writable inputs.
    raw_api, raw_tools = blob(root, product, OPENAPI), blob(root, product, MCP)
    api_hash, mcp_hash = sha(raw_api), sha(raw_tools)
    catalog = json.loads(raw_tools)
    check_catalog_provenance(root, product, raw_api, raw_tools)

    contracts = sdk / 'sdkgen/contracts'
    metadata = contracts / 'SOURCE.json'
    baseline_args = []
    expected_source = {'backend_commit': product, 'openapi_sha256': api_hash, 'mcp_sha256': mcp_hash}
    recorded = json.loads(metadata.read_bytes()) if metadata.exists() else {}
    scoped_keys = {'non_skills_sdk_commit', 'non_skills_slice_sha256'} & recorded.keys()
    if scoped_keys and len(scoped_keys) != 2:
        raise ValueError('SDK SOURCE.json has partial Skills baseline metadata')
    # Scoped preview: both baseline fields present. Full commit-pinned sync (SOURCE.json
    # without them) and released checkouts (no SOURCE.json) regenerate in full mode.
    if scoped_keys:
        baseline = recorded['non_skills_sdk_commit']
        if not re.fullmatch(r'[0-9a-f]{40}', str(baseline)):
            raise ValueError('SDK SOURCE.json baseline is not a full commit')
        baseline_hash = sha(blob(sdk, baseline, 'sdkgen/contracts/swagger.json'))
        expected_source.update(non_skills_sdk_commit=baseline, non_skills_slice_sha256=baseline_hash)
        baseline_args = ['--skills-baseline-sdk', baseline]

    with tempfile.TemporaryDirectory() as tmp:
        api_input, mcp_input = Path(tmp) / 'swagger.json', Path(tmp) / 'tools.generated.json'
        api_input.write_bytes(raw_api)
        mcp_input.write_bytes(raw_tools)
        run_sdk(sdk, 'sdkgen/sync_contract.py', api_input, '--backend-commit', product,
                '--mcp-tools', mcp_input, *baseline_args)
        if api_input.read_bytes() != raw_api or mcp_input.read_bytes() != raw_tools:
            raise ValueError('SDK sync changed its committed contract inputs')
    run_sdk(sdk, 'sdkgen/generate.py', '--only', 'python', 'typescript')
    run_sdk(sdk, 'sdkgen/generate.py', '--check', '--only', 'python', 'typescript')
    if baseline_args:
        run_sdk(sdk, 'sdkgen/check_skills_baseline.py')
    check_changes(sdk)
    files = {name: (contracts / name).read_bytes() for name in CONTRACTS}
    swagger, tools, source = (json.loads(files[name]) for name in CONTRACTS)
    if (source != expected_source or swagger['x-source-sha256'] != api_hash or tools['x-source-sha256'] != mcp_hash
            or swagger.get('x-skills-only-baseline-sha256') != expected_source.get('non_skills_slice_sha256')):
        raise ValueError('Regenerated snapshot does not match the committed contract inputs')
    if tools['tools'] != [{'name': name} for name in sorted(t['name'] for t in catalog['tools'])]:
        raise ValueError('SDK tool inventory does not match the tested catalog')
    sdk_catalog = yaml.safe_load((sdk / 'sdkgen/catalog/operations.yaml').read_bytes())
    coverage = check_slice(json.loads(raw_api), swagger, catalog, sdk_catalog)
    return {'sync_mode': 'scoped' if baseline_args else 'full', 'source': source, 'coverage': coverage,
            'sha256': {name: sha(data) for name, data in files.items()}}


if __name__ == '__main__':
    print(json.dumps(regenerate(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
