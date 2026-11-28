"""Regenerate the SDK snapshot and compare the shipped Skills subset to committed product inputs."""

import hashlib
import importlib.util
import json
import re
import runpy
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
LEGACY_CONTRACTS = ('swagger.json', 'mcp-tools.json', 'SOURCE.json')
SDK_SWAGGER = 'sdkgen/contracts/swagger.json'
# The driver runs with -I; share the receipt schema without changing its import path.
verifier = runpy.run_path(str(Path(__file__).with_name('verify-skill-packages.py')))
# Exact generator-owned paths, not entire directories: per-resource subclasses and
# catalog entries are hand-written. A new scaffold must be committed in the SDK first.
GENERATED = (
    {f'python/fi/platform/{name}' for name in (
        '_wire_definitions.json', 'models.py', '_operations.py', 'resources/base.py', 'resources/__init__.py',
    )}
    | {f'typescript/futureagi/src/platform/{name}' for name in (
        '_wireDefinitions.ts', 'models.ts', '_operations.ts', 'resources/base.ts', 'resources/index.ts',
    )}
)


def check_changes(sdk, outputs=('swagger.json',)):
    def paths(*args):
        return set(subprocess.check_output(['git', '-C', sdk, *args], text=True).split('\0')) - {''}

    # Include staged, unstaged and untracked files (even ignored files). Generator
    # commands run with -B so their own bytecode is not a checkout change.
    changed = paths('diff', '--no-renames', '--name-only', '-z', 'HEAD') | paths('ls-files', '--others', '-z')
    unexpected = changed - GENERATED - {f'sdkgen/contracts/{name}' for name in outputs}
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


def skills_inputs(doc, method, path):
    """(location, name) -> required for one operation's inputs, request body fields included."""
    op = effective_operation(doc, method, path)
    if op is None:
        return None
    inputs = {}
    for (location, name), param in op['parameters'].items():
        if location != 'body':
            inputs[location, name] = bool(param.get('required'))
            continue
        schema = param.get('schema') or {}
        if '$ref' in schema:
            schema = inline_reusable(doc, referenced(doc, schema['$ref']))
        required = set(schema.get('required') or ())
        for field in {*schema.get('properties', {}), *required}:
            inputs['body', field] = field in required
    return inputs


def breaking_skills_changes(shipped, product):
    """What a client of the shipped Skills contract would break on: a route the product no longer
    serves, a required input it removed or renamed, or an input it newly requires. The regenerated
    SDK follows the product, so this compares the paired SDK's committed slice with the product."""
    problems = []
    for path, item in sorted(shipped.get('paths', {}).items()):
        if not path.startswith(SKILLS):
            continue
        for method in sorted(m for m in item if m in HTTP_METHODS):
            before = skills_inputs(shipped, method, path)
            after = skills_inputs(product, method, path)
            if after is None:
                problems.append(f'{method.upper()} {path} is no longer served')
                continue
            for (location, name), required in sorted(before.items()):
                if required and (location, name) not in after:
                    problems.append(f'{method.upper()} {path}: required {location} input {name!r} was removed or renamed')
            for (location, name), required in sorted(after.items()):
                if required and not before.get((location, name)):
                    problems.append(f'{method.upper()} {path}: {location} input {name!r} is newly required')
    return problems


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
        raise ValueError(f'SDK MCP tool absent or bound differently in the tested backend: {name}')
    hints = tool.get('annotations', {})
    if 'access' in declared:
        access = declared['access']
        if (access not in {'read', 'write', 'destructive'}
                or hints.get('readOnlyHint') is not (access == 'read')
                or hints.get('destructiveHint') is not (access == 'destructive')):
            raise ValueError(f'SDK MCP access differs for {name}')
    if 'open_world' in declared and hints.get('openWorldHint') is not declared['open_world']:
        raise ValueError(f'SDK MCP open_world differs for {name}')
    for param, cap in declared.get('max_page_size', {}).items():
        schema = tool['inputSchema']['properties'].get(param, {})
        mapping = tool['request']['parameters']
        if (param not in [*mapping['query'], *mapping['body']] or schema.get('type') != 'integer'
                or type(schema.get('maximum')) is not int or type(cap) is not int or schema['maximum'] != cap):
            raise ValueError(f'SDK MCP max_page_size differs for {name}.{param}')
    # Other explicit fields (including group or stability) must have an equal
    # backend value. Do not silently accept new, unmapped catalog settings.
    for key in declared.keys() - {'access', 'open_world', 'max_page_size'}:
        # The SDK defaults an omitted stability to 'stable' and the backend omits it.
        if key == 'stability' and declared[key] == 'stable' and tool.get(key, 'stable') == 'stable':
            continue
        if key not in tool or declared[key] != tool[key]:
            raise ValueError(f'SDK MCP {key} differs for {name}')


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
    # Ignore Python environment/user site and skip bytecode, but keep sdkgen/ on
    # sys.path: sync_scoped_contract.py imports sibling modules. Reserve stdout for the receipt.
    subprocess.run([sys.executable, '-EBs', *args], cwd=sdk, stdout=sys.stderr, check=True)


def generate_clients(sdk, *, check_baseline, paired=None):
    run_sdk(sdk, 'sdkgen/generate.py', '--only', 'python', 'typescript')
    run_sdk(sdk, 'sdkgen/generate.py', '--check', '--only', 'python', 'typescript')
    if check_baseline:
        # paired=(sdk_commit, product): everything but Skills must match that SDK commit exactly.
        extra = ('--paired', paired[0], '--product-commit', paired[1]) if paired else ()
        run_sdk(sdk, 'sdkgen/check_skills_baseline.py', *extra)


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


def shipped_sql_tools(sdk_catalog, product_catalog):
    """SQL shares the observability group; only sql.* operations with MCP mappings ship in this scope."""
    product_tools = {tool['name']: tool for tool in product_catalog['tools']}
    names = set()
    for entry in sdk_catalog['operations']:
        if not entry['id'].startswith('sql.'):
            continue
        mapping = entry['surfaces'].get('mcp')
        if mapping is None or mapping is False:
            continue
        name = mapping.get('name') or entry['id'].replace('.', '_')
        if name in names:
            raise ValueError(f'Duplicate SDK SQL MCP tool: {name}')
        op = entry['operation']
        check_tool(name, (op['method'].upper(), op['path']), mapping, product_tools.get(name))
        names.add(name)
    return sorted(names)


# Product pairing owns only the Skills scope. SQL, Epoch and the unscoped operations stay exactly
# as the paired SDK commit ships them: the SDK's own pairing compares those with the product, so
# a product change to them, or an SDK change that lands first, can never fail this build.
OWNED_SCOPES = ('skills',)


def regenerate_scoped(root, sdk, sdk_commit, snapshot, inputs, catalog, sdk_catalog):
    contracts = sdk / 'sdkgen/contracts'
    if ({'x-source-sha256', 'x-skills-only-baseline-sha256'} & snapshot.keys()
            or any((contracts / name).exists() for name in ('SOURCE.json', 'mcp-tools.json'))):
        raise ValueError('Mixed legacy and scoped contract provenance')
    recorded = snapshot['x-contract-sources']
    verifier['verify_scoped_source'](recorded)
    baseline = recorded['other']['sdk_commit']
    baseline_raw = blob(sdk, baseline, SDK_SWAGGER)
    old = json.loads(baseline_raw)
    # The baseline itself may have already synced SQL/Epoch; its release OpenAPI
    # digest is distinct from the digest of its actual Swagger slice.
    release_hash = (old['x-contract-sources']['other']['openapi_sha256'] if 'x-contract-sources' in old
                    else old['x-source-sha256'])
    if recorded['other'] != {'sdk_commit': baseline, 'slice_sha256': sha(baseline_raw), 'openapi_sha256': release_hash}:
        raise ValueError('Scoped SDK baseline does not match its committed slice')

    product = inputs['backend_commit']
    inputs = {**inputs, 'tools_yaml_sha256': sha(blob(root, product, MCP_CATALOG))}
    # Skills takes its complete group from the tested product's generated MCP inventory; every
    # other record is the paired SDK commit's own, byte for byte.
    expected = {**recorded, **{scope: {**inputs, 'tools': sorted(t['name'] for t in catalog['tools']
                                                                  if t['group'] == scope)}
                               for scope in OWNED_SCOPES}}
    verifier['verify_scoped_source'](expected, product, owned=OWNED_SCOPES)
    if any(recorded[scope]['backend_commit'] == product and recorded[scope] != expected[scope]
           for scope in OWNED_SCOPES):
        raise ValueError('Recorded scoped inputs disagree with the same product commit')

    run_sdk(sdk, 'sdkgen/sync_scoped_contract.py', root.resolve(), product, sdk_commit,
            '--scopes', *OWNED_SCOPES)
    generate_clients(sdk, check_baseline=True, paired=(sdk_commit, product))
    check_changes(sdk)
    raw = (sdk / SDK_SWAGGER).read_bytes()
    swagger = json.loads(raw)
    source = swagger.get('x-contract-sources')
    if source != expected or {'x-source-sha256', 'x-skills-only-baseline-sha256'} & swagger.keys():
        raise ValueError('Regenerated scoped snapshot does not match the committed contract inputs')
    breaking = breaking_skills_changes(snapshot, swagger)
    if breaking:
        raise ValueError('Breaking change to the Skills contract the paired SDK ships (pair a same-named '
                         'SDK branch that ships it): ' + '; '.join(breaking[:10]))
    return swagger, {'sync_mode': 'scoped', 'owned_scopes': list(OWNED_SCOPES), 'source': source,
                     'sha256': {'swagger.json': sha(raw)}}


def regenerate_legacy(sdk, snapshot, inputs, raw_api, raw_tools, catalog):
    contracts = sdk / 'sdkgen/contracts'
    metadata = contracts / 'SOURCE.json'
    inventory = contracts / 'mcp-tools.json'
    if (not verifier['hex_digest'](snapshot.get('x-source-sha256'), 64) or metadata.exists() != inventory.exists()):
        raise ValueError('Missing or partial legacy contract provenance')
    expected_source = dict(inputs)
    product = inputs['backend_commit']
    baseline_args = []
    if metadata.exists():
        recorded = json.loads(metadata.read_bytes())
        mode = verifier['verify_legacy_source'](recorded)
        if (recorded['openapi_sha256'] != snapshot['x-source-sha256']
                or recorded['mcp_sha256'] != json.loads(inventory.read_bytes()).get('x-source-sha256')
                or ('x-skills-only-baseline-sha256' in snapshot) != (mode == 'scoped')):
            raise ValueError('Inconsistent legacy contract provenance')
        if mode == 'scoped':
            baseline = recorded['non_skills_sdk_commit']
            baseline_hash = sha(blob(sdk, baseline, SDK_SWAGGER))
            if (baseline_hash != recorded['non_skills_slice_sha256']
                    or baseline_hash != snapshot['x-skills-only-baseline-sha256']):
                raise ValueError('Legacy SDK baseline does not match its committed slice')
            expected_source.update(non_skills_sdk_commit=baseline, non_skills_slice_sha256=baseline_hash)
            baseline_args = ['--skills-baseline-sdk', baseline]
    elif ('x-skills-only-baseline-sha256' in snapshot or not (contracts / 'BACKEND_VERSION').is_file()
          or not re.fullmatch(r'v\d+\.\d+\.\d+', (contracts / 'BACKEND_VERSION').read_text().strip())):
        raise ValueError('No complete release or commit-pinned contract provenance')

    with tempfile.TemporaryDirectory() as tmp:
        api_input, mcp_input = Path(tmp) / 'swagger.json', Path(tmp) / 'tools.generated.json'
        api_input.write_bytes(raw_api)
        mcp_input.write_bytes(raw_tools)
        run_sdk(sdk, 'sdkgen/sync_contract.py', api_input, '--backend-commit', product,
                '--mcp-tools', mcp_input, *baseline_args)
        if api_input.read_bytes() != raw_api or mcp_input.read_bytes() != raw_tools:
            raise ValueError('SDK sync changed its committed contract inputs')
    generate_clients(sdk, check_baseline=bool(baseline_args))
    check_changes(sdk, LEGACY_CONTRACTS)
    files = {name: (contracts / name).read_bytes() for name in LEGACY_CONTRACTS}
    swagger, tools, source = (json.loads(files[name]) for name in LEGACY_CONTRACTS)
    if (source != expected_source or 'x-contract-sources' in swagger
            or swagger['x-source-sha256'] != inputs['openapi_sha256']
            or tools['x-source-sha256'] != inputs['mcp_sha256']
            or swagger.get('x-skills-only-baseline-sha256') != expected_source.get('non_skills_slice_sha256')):
        raise ValueError('Regenerated snapshot does not match the committed contract inputs')
    if tools['tools'] != [{'name': name} for name in sorted(t['name'] for t in catalog['tools'])]:
        raise ValueError('SDK tool inventory does not match the tested catalog')
    return swagger, {'sync_mode': 'scoped' if baseline_args else 'full', 'source': source,
                     'sha256': {name: sha(data) for name, data in files.items()}}


def regenerate(root, sdk, product):
    # Select the layout from the paired commit and compute expected hashes from
    # Git objects, independently of both the working tree and generator outputs.
    sdk_commit = subprocess.check_output(['git', '-C', sdk, 'rev-parse', 'HEAD'], text=True).strip()
    snapshot = json.loads(blob(sdk, sdk_commit, SDK_SWAGGER))
    raw_api, raw_tools = blob(root, product, OPENAPI), blob(root, product, MCP)
    inputs = {'backend_commit': product, 'openapi_sha256': sha(raw_api), 'mcp_sha256': sha(raw_tools)}
    catalog = json.loads(raw_tools)
    check_catalog_provenance(root, product, raw_api, raw_tools)
    sdk_catalog = yaml.safe_load(blob(sdk, sdk_commit, 'sdkgen/catalog/operations.yaml'))
    if 'x-contract-sources' in snapshot:
        swagger, receipt = regenerate_scoped(root, sdk, sdk_commit, snapshot, inputs, catalog, sdk_catalog)
    else:
        swagger, receipt = regenerate_legacy(sdk, snapshot, inputs, raw_api, raw_tools, catalog)
    return {**receipt, 'coverage': check_slice(json.loads(raw_api), swagger, catalog, sdk_catalog)}


if __name__ == '__main__':
    print(json.dumps(regenerate(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])))
