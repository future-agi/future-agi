"""Controls for branch matching and the regenerated Skills contract."""

import copy
import hashlib
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
resolve = runpy.run_path(str(HERE / 'resolve-skill-refs.py'))['resolve']
checker = runpy.run_path(str(HERE / 'check-skill-contract.py'))
check_slice = checker['check_slice']


def listing(*branches):
    return ''.join(f'{index:040x}\trefs/heads/{branch}\n' for index, branch in enumerate(branches, 1))


class BranchTests(unittest.TestCase):
    def test_independent_order_and_exact_branch_names(self):
        sdk = listing('dev', 'release', 'feature/x')
        trace = listing('dev', 'release', 'other/feature/x') + 'a' * 40 + '\trefs/tags/feature/x\n'
        with patch('subprocess.check_output', side_effect=[sdk, trace]):
            refs = resolve('feature/x', 'release')
        self.assertEqual(refs['sdk'], {'branch': 'feature/x', 'commit': f'{3:040x}'})
        self.assertEqual(refs['traceai'], {'branch': 'release', 'commit': f'{2:040x}'})

    def test_fallback_and_failure(self):
        with patch('subprocess.check_output', side_effect=[listing('main', 'dev'), listing('main')]):
            refs = resolve('unpaired', '')
        self.assertEqual([ref['branch'] for ref in refs.values()], ['dev', 'main'])
        with patch('subprocess.check_output', return_value=listing('other')), self.assertRaisesRegex(ValueError, 'No matching'):
            resolve('unpaired', '')
        error = subprocess.CalledProcessError(128, ['git', 'ls-remote'])
        with patch('subprocess.check_output', side_effect=error), self.assertRaises(subprocess.CalledProcessError):
            resolve('feature/x', 'dev')


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.path = '/model-hub/skills/'
        self.api = {'paths': {self.path: {'parameters': [], 'get': {'$ref': '#/definitions/Page'}}},
                    'definitions': {'Page': {'items': {'$ref': '#/definitions/Skill'}}, 'Skill': {'type': 'string'}}}
        self.swagger = copy.deepcopy(self.api)
        self.catalog = {'tools': [{
            'name': 'list_skills', 'group': 'skills',
            'request': {'method': 'GET', 'path': self.path, 'parameters': {'query': ['size'], 'body': []}},
            'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False},
            'inputSchema': {'properties': {'size': {'type': 'integer', 'maximum': 25}}},
        }]}
        self.entry = {'id': 'skills.list', 'operation': {'method': 'GET', 'path': self.path},
                      'surfaces': {'sdk': {}, 'mcp': {'name': 'list_skills', 'group': 'skills', 'access': 'read'}}}

    def check(self):
        return check_slice(self.api, self.swagger, self.catalog, {'operations': [self.entry]})

    def test_unwrapped_backend_operations_are_reported_not_fatal(self):
        self.assertEqual(self.check()['skills_operations']['not_wrapped'], [])
        self.api['responses'] = {'Accepted': {'description': 'Queued'}}
        self.api['paths'][self.path]['post'] = {'responses': {'202': {'$ref': '#/responses/Accepted'}}}
        self.assertEqual(self.check()['skills_operations']['not_wrapped'], [f'POST {self.path}'])

    def test_shipped_operation_must_exist_unchanged_in_backend(self):
        del self.api['paths'][self.path]['get']
        with self.assertRaisesRegex(ValueError, 'does not expose'):
            self.check()
        self.api['paths'][self.path]['get'] = {'$ref': '#/definitions/Page'}
        self.swagger['paths'][self.path]['get'] = {'$ref': '#/definitions/Skill'}
        with self.assertRaisesRegex(ValueError, 'operation differs'):
            self.check()
        self.swagger['paths'][self.path]['get'] = {'$ref': '#/definitions/Page'}
        self.swagger['definitions']['Skill']['type'] = 'integer'
        with self.assertRaisesRegex(ValueError, 'models differ'):
            self.check()

    def test_effective_path_parameter_is_part_of_shipped_operation(self):
        param = {'in': 'query', 'name': 'limit', 'type': 'integer', 'minimum': 1}
        self.api['paths'][self.path]['parameters'] = [param]
        self.swagger['paths'][self.path]['parameters'] = [{**param, 'minimum': 0}]
        with self.assertRaisesRegex(ValueError, 'operation differs'):
            self.check()
        # The method overrides this parameter on both sides.
        for doc in (self.api, self.swagger):
            doc['paths'][self.path]['get']['parameters'] = [{**param, 'minimum': 3}]
        self.check()

    def test_reusable_parameters_and_responses_of_shipped_operation(self):
        param = {'in': 'body', 'name': 'body', 'schema': {'$ref': '#/definitions/Skill'}}
        response = {'description': 'OK'}
        self.api.update(parameters={'Body': param}, responses={'OK': response})
        self.api['paths'][self.path] = {'parameters': [{'$ref': '#/parameters/Body'}],
                                        'get': {'responses': {'200': {'$ref': '#/responses/OK'}}}}
        self.swagger['paths'][self.path] = {'parameters': [copy.deepcopy(param)],
                                            'get': {'responses': {'200': copy.deepcopy(response)}}}
        self.check()
        self.swagger['definitions']['Skill']['type'] = 'integer'
        with self.assertRaisesRegex(ValueError, 'models differ'):
            self.check()

    def test_unwrapped_tools_and_same_binding_different_settings(self):
        self.catalog['tools'].append({'name': 'another_tool', 'request': {'method': 'GET', 'path': self.path}})
        declared = self.entry['surfaces']['mcp']
        declared.update(max_page_size={'size': 25}, open_world=False)
        self.assertEqual(self.check()['skills_mcp_tools']['not_wrapped'], ['another_tool'])
        for key, value in [('group', 'other'), ('access', 'write'), ('access', 'destructive'),
                           ('open_world', True), ('max_page_size', {'size': 100}), ('stability', 'beta')]:
            with self.subTest(key=key, value=value), patch.dict(declared, {key: value}):
                with self.assertRaisesRegex(ValueError, key):
                    self.check()
        declared['access'] = 'write'
        hints = self.catalog['tools'][0]['annotations']
        hints['readOnlyHint'] = False
        self.check()
        hints['destructiveHint'] = True
        with self.assertRaisesRegex(ValueError, 'access'):
            self.check()

    def test_snapshot_alone_is_not_a_shipped_surface(self):
        self.entry['surfaces']['sdk'] = False
        with self.assertRaisesRegex(ValueError, 'ships no Skills operations'):
            self.check()
        self.entry['surfaces']['sdk'] = {}
        self.catalog['tools'][0]['request']['method'] = 'POST'
        with self.assertRaisesRegex(ValueError, 'bound differently'):
            self.check()

    def test_header_names_are_case_insensitive_for_overrides(self):
        inherited = {'in': 'header', 'name': 'X-Context-Resolution', 'type': 'string', 'enum': ['a']}
        override = {'in': 'header', 'name': 'x-context-resolution', 'type': 'string', 'enum': ['b']}
        for doc in (self.api, self.swagger):
            doc['paths'][self.path]['parameters'] = [inherited]
            doc['paths'][self.path]['get']['parameters'] = [override]
        self.check()
        # Only the effective (method) declaration counts on each side.
        self.swagger['paths'][self.path]['get']['parameters'] = [{**override, 'enum': ['c']}]
        with self.assertRaisesRegex(ValueError, 'operation differs'):
            self.check()

    def test_explicit_stable_equals_the_default(self):
        self.entry['surfaces']['mcp']['stability'] = 'stable'
        self.check()

    def test_released_sdk_syncs_committed_inputs_without_a_preview_baseline(self):
        regenerate = checker['regenerate']
        with tempfile.TemporaryDirectory() as tmp:
            product, sdk = Path(tmp) / 'product', Path(tmp) / 'sdk'
            api_bytes = json.dumps(self.api).encode()
            api_sha = hashlib.sha256(api_bytes).hexdigest()
            tools_bytes = json.dumps({**self.catalog, 'source_contract_sha256': api_sha}).encode()
            tools_sha = hashlib.sha256(tools_bytes).hexdigest()

            def commit(repo, files):
                for name, content in files.items():
                    path = repo / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                def git(*args):
                    return subprocess.check_output(['git', '-C', repo, *args], text=True).strip()
                git('init', '-q')
                git('add', '.')
                git('-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')

            revision = commit(product, {checker['OPENAPI']: api_bytes, checker['MCP']: tools_bytes})
            commit(sdk, {'sdkgen/catalog/operations.yaml': json.dumps({'operations': [self.entry]}).encode(),
                         'sdkgen/contracts/swagger.json': b'{}', 'sdkgen/contracts/BACKEND_VERSION': b'v1.0.0'})
            # A released SDK has neither SOURCE.json nor mcp-tools.json. A
            # writable product tree is not the input or the provenance authority.
            (product / checker['OPENAPI']).write_text('{}')
            (product / checker['MCP']).write_text('{}')
            calls = []
            source = {'backend_commit': revision, 'openapi_sha256': api_sha, 'mcp_sha256': tools_sha}

            def fixture_run(repo, script, *args):
                self.assertEqual(repo, sdk)
                calls.append((script, *args))
                if script == 'sdkgen/sync_contract.py':
                    api, commit_flag, sha, mcp_flag, mcp = args  # full commit mode has no baseline flag
                    self.assertEqual((commit_flag, sha, mcp_flag), ('--backend-commit', revision, '--mcp-tools'))
                    self.assertEqual((api.read_bytes(), mcp.read_bytes()), (api_bytes, tools_bytes))
                    snapshots = {'SOURCE.json': source, 'swagger.json': {**self.swagger, 'x-source-sha256': api_sha},
                                 'mcp-tools.json': {'tools': [{'name': 'list_skills'}], 'x-source-sha256': tools_sha}}
                    for name, value in snapshots.items():
                        (sdk / 'sdkgen/contracts' / name).write_text(json.dumps(value))

            with patch.dict(regenerate.__globals__, run_sdk=fixture_run, check_catalog_provenance=lambda *_: None):
                receipt = regenerate(product, sdk, revision)
            self.assertEqual(receipt['sync_mode'], 'full')
            # A full commit-pinned sync leaves SOURCE.json without baseline fields; it also selects full mode.
            (sdk / 'sdkgen/contracts/SOURCE.json').write_text(json.dumps({'backend_commit': 'f' * 40}))
            subprocess.check_call(['git', '-C', sdk, 'add', '.'])
            subprocess.check_call(['git', '-C', sdk, '-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'full sync'])
            calls.clear()
            with patch.dict(regenerate.__globals__, run_sdk=fixture_run, check_catalog_provenance=lambda *_: None):
                self.assertEqual(regenerate(product, sdk, revision)['sync_mode'], 'full')
            # Partial baseline metadata is rejected rather than guessed.
            (sdk / 'sdkgen/contracts/SOURCE.json').write_text(json.dumps({'non_skills_sdk_commit': 'a' * 40}))
            subprocess.check_call(['git', '-C', sdk, 'add', '.'])
            subprocess.check_call(['git', '-C', sdk, '-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'partial'])
            with patch.dict(regenerate.__globals__, run_sdk=fixture_run, check_catalog_provenance=lambda *_: None), self.assertRaisesRegex(ValueError, 'partial'):
                regenerate(product, sdk, revision)
            self.assertEqual(receipt['source'], source)
            self.assertEqual(calls[1:], [('sdkgen/generate.py', '--only', 'python', 'typescript'),
                                         ('sdkgen/generate.py', '--check', '--only', 'python', 'typescript')])

    def test_catalog_must_be_what_the_committed_generator_renders(self):
        provenance = checker['check_catalog_provenance']
        generator = (b"import json\n"
                     b"def generate_tool_manifest(contract_path, catalog_path):\n"
                     b"    contract = json.loads(contract_path.read_text())\n"
                     b"    return {'source_contract': contract_path.name, 'tool_count': len(contract['paths']),"
                     b" 'tools': [{'name': n} for n in sorted(contract['paths'])]}\n")
        api = json.dumps({'swagger': '2.0', 'paths': {'/a/': {}, '/b/': {}}}).encode()
        rendered = (json.dumps({'source_contract': 'swagger.json', 'tool_count': 2,
                                'tools': [{'name': '/a/'}, {'name': '/b/'}]}, indent=2) + '\n').encode()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            package = {checker['MCP_GENERATOR']: generator, checker['MCP_CATALOG']: b'tools: {}\n',
                       checker['MCP_PACKAGE'] + '/__init__.py': b'',
                       checker['MCP_PACKAGE'] + '/request_headers.py': b'ALLOWED = ()\n'}
            generator = b"from mcp_server.request_headers import ALLOWED\n" + generator
            package[checker['MCP_GENERATOR']] = generator
            for name, content in package.items():
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_bytes(content)
            subprocess.check_call(['git', '-C', repo, 'init', '-q'])
            subprocess.check_call(['git', '-C', repo, 'add', '.'])
            subprocess.check_call(['git', '-C', repo, '-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'gen'])
            revision = subprocess.check_output(['git', '-C', repo, 'rev-parse', 'HEAD'], text=True).strip()
            provenance(repo, revision, api, rendered)
            # Any byte that the committed generator would not produce is rejected,
            # whatever metadata fields the manifest carries.
            for stale in (rendered.replace(b'"/b/"', b'"/c/"'), rendered[:-1], json.dumps({'x': 1}).encode()):
                with self.assertRaisesRegex(ValueError, 'not generated from the committed OpenAPI'):
                    provenance(repo, revision, api, stale)

    def test_regeneration_rejects_handwritten_changes_and_new_scaffolds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                subprocess.run(['git', '-C', root, *args], check=True, capture_output=True)
            git('init', '-q')
            catalog = root / 'catalog.yaml'
            catalog.write_text('initial')
            git('add', '.')
            git('-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture')
            generated = root / 'sdkgen/contracts/swagger.json'
            generated.parent.mkdir(parents=True)
            generated.write_text(json.dumps(self.api))
            checker['check_changes'](root)
            catalog.write_text('changed')
            git('add', 'catalog.yaml')
            with self.assertRaisesRegex(ValueError, 'catalog.yaml'):
                checker['check_changes'](root)
            git('restore', '--staged', '--worktree', 'catalog.yaml')
            (root / 'new_resource.py').touch()
            with self.assertRaisesRegex(ValueError, 'new_resource.py'):
                checker['check_changes'](root)
