"""Controls for branch matching and the regenerated Skills contract."""

import copy
import hashlib
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
resolve = runpy.run_path(str(HERE / 'resolve-skill-refs.py'))['resolve']
checker = runpy.run_path(str(HERE / 'check-skill-contract.py'))
check_slice = checker['check_slice']


def listing(*branches):
    return ''.join(f'{index:040x}\trefs/heads/{branch}\n' for index, branch in enumerate(branches, 1))


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
        with patch('subprocess.check_output', side_effect=error), patch('time.sleep') as sleep, \
                self.assertRaisesRegex(RuntimeError, 'transport error, not an absent branch'):
            resolve('feature/x', 'dev')
        self.assertEqual([c.args for c in sleep.call_args_list], [(5,), (15,), (45,)])

    def test_transient_listing_failures_are_retried_not_read_as_absent(self):
        timeout = subprocess.TimeoutExpired(['git', 'ls-remote'], 60)
        error = subprocess.CalledProcessError(128, ['git', 'ls-remote'])
        sdk, trace = listing('dev', 'feature/x'), listing('dev')
        with patch('subprocess.check_output', side_effect=[timeout, error, sdk, trace]) as run, \
                patch('time.sleep') as sleep:
            refs = resolve('feature/x', 'dev')
        self.assertEqual(refs['sdk']['branch'], 'feature/x')  # not a fallback to dev
        self.assertEqual(run.call_count, 4)
        self.assertEqual([c.args for c in sleep.call_args_list], [(5,), (15,)])


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

            revision = commit(product, {checker['OPENAPI']: api_bytes, checker['MCP']: tools_bytes})
            commit(sdk, {'sdkgen/catalog/operations.yaml': json.dumps({'operations': [self.entry]}).encode(),
                         'sdkgen/contracts/swagger.json': json.dumps({'x-source-sha256': 'f' * 64}).encode(),
                         'sdkgen/contracts/BACKEND_VERSION': b'v1.0.0'})
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
            commit(sdk, {})
            calls.clear()
            with patch.dict(regenerate.__globals__, run_sdk=fixture_run, check_catalog_provenance=lambda *_: None):
                self.assertEqual(regenerate(product, sdk, revision)['sync_mode'], 'full')
            # Partial baseline metadata is rejected rather than guessed.
            partial = {**source, 'non_skills_sdk_commit': 'a' * 40}
            commit(sdk, {'sdkgen/contracts/SOURCE.json': json.dumps(partial).encode()})
            with patch.dict(regenerate.__globals__, run_sdk=fixture_run, check_catalog_provenance=lambda *_: None), self.assertRaisesRegex(ValueError, 'partial'):
                regenerate(product, sdk, revision)
            self.assertEqual(receipt['source'], source)
            self.assertEqual(calls[1:], [('sdkgen/generate.py', '--only', 'python', 'typescript'),
                                         ('sdkgen/generate.py', '--check', '--only', 'python', 'typescript')])

    def test_a_legacy_layout_sdk_is_guarded_against_breaking_skills_changes_too(self):
        regenerate = checker['regenerate']
        legacy = {'name': 'legacy', 'in': 'query', 'required': True, 'type': 'string'}
        with tempfile.TemporaryDirectory() as tmp:
            product, sdk = Path(tmp) / 'product', Path(tmp) / 'sdk'
            api_bytes = json.dumps(self.api).encode()
            api_sha = hashlib.sha256(api_bytes).hexdigest()
            tools_bytes = json.dumps({**self.catalog, 'source_contract_sha256': api_sha}).encode()
            tools_sha = hashlib.sha256(tools_bytes).hexdigest()
            revision = commit(product, {checker['OPENAPI']: api_bytes, checker['MCP']: tools_bytes})

            def fixture_run(repo, script, *args):
                if script == 'sdkgen/sync_contract.py':
                    source = {'backend_commit': revision, 'openapi_sha256': api_sha, 'mcp_sha256': tools_sha}
                    snapshots = {'SOURCE.json': source, 'swagger.json': {**self.swagger, 'x-source-sha256': api_sha},
                                 'mcp-tools.json': {'tools': [{'name': 'list_skills'}], 'x-source-sha256': tools_sha}}
                    for name, value in snapshots.items():
                        (sdk / 'sdkgen/contracts' / name).write_text(json.dumps(value))

            for paths, breaks in [(self.swagger['paths'], False),
                                  ({self.path: {'get': {'parameters': [legacy]}}}, True)]:
                with self.subTest(breaks=breaks):
                    for name in ('SOURCE.json', 'mcp-tools.json'):
                        (sdk / 'sdkgen/contracts' / name).unlink(missing_ok=True)
                    commit(sdk, {'sdkgen/catalog/operations.yaml': json.dumps({'operations': [self.entry]}).encode(),
                                 'sdkgen/contracts/swagger.json': json.dumps(
                                     {'x-source-sha256': 'f' * 64, 'paths': paths}).encode(),
                                 'sdkgen/contracts/BACKEND_VERSION': b'v1.0.0'})
                    with patch.dict(regenerate.__globals__, run_sdk=fixture_run,
                                    check_catalog_provenance=lambda *_: None):
                        if breaks:
                            with self.assertRaisesRegex(ValueError, "Breaking change.*required query input 'legacy' was removed"):
                                regenerate(product, sdk, revision)
                        else:
                            self.assertEqual(regenerate(product, sdk, revision)['sync_mode'], 'full')

    def test_scoped_sync_uses_committed_inputs_and_the_recorded_sdk_baseline(self):
        regenerate = checker['regenerate']

        def encode(value):
            return json.dumps(value).encode()

        def sha(raw):
            return hashlib.sha256(raw).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root, sdk = Path(tmp) / 'product', Path(tmp) / 'sdk'
            tools = {'sql': ['cancel_sql', 'run_sql'], 'epoch': ['get_epoch'], 'skills': ['list_skills']}
            catalog = copy.deepcopy(self.catalog)
            sdk_ops = [self.entry]
            for op_id, name, path in [('sql.run', 'run_sql', '/tracer/sql-query/run/'),
                                      ('sql.cancel', 'cancel_sql', '/tracer/sql-query/{id}/cancel/')]:
                endpoint = {'method': 'POST', 'path': path}
                mapping = {'name': name, 'group': 'observability'}
                catalog['tools'].append({**mapping, 'request': endpoint})
                sdk_ops.append({'id': op_id, 'operation': endpoint, 'surfaces': {'mcp': mapping}})
            for name, group in [('list_traces', 'observability'), ('get_epoch', 'epoch')]:
                catalog['tools'].append({'name': name, 'group': group, 'request': {'path': '/unrelated/'}})
            api_bytes, mcp_bytes, yaml_bytes = encode(self.api), encode(catalog), b'tools: {}\n'
            revision = commit(root, {checker['OPENAPI']: api_bytes, checker['MCP']: mcp_bytes,
                                     checker['MCP_CATALOG']: yaml_bytes})
            # A baseline can already be a scoped snapshot from before Skills existed.
            old_inputs = {'backend_commit': '1' * 40, 'openapi_sha256': '2' * 64,
                          'mcp_sha256': '3' * 64, 'tools_yaml_sha256': '4' * 64}
            release_hash = '5' * 64
            older = {k: {**old_inputs, 'tools': tools[k]} for k in ('sql', 'epoch')}
            older['other'] = {'sdk_commit': '6' * 40, 'slice_sha256': '7' * 64, 'openapi_sha256': release_hash}
            baseline_raw = encode({'x-contract-sources': older})
            path = 'sdkgen/contracts/swagger.json'
            baseline = commit(sdk, {path: baseline_raw})
            recorded = {k: {**old_inputs, 'tools': names} for k, names in tools.items()}
            recorded['other'] = {'sdk_commit': baseline, 'slice_sha256': sha(baseline_raw),
                                 'openapi_sha256': release_hash}
            head = commit(sdk, {path: encode({'x-contract-sources': recorded}),
                                'sdkgen/catalog/operations.yaml': encode({'operations': sdk_ops})})
            # Product pairing owns Skills only: sql, epoch and other stay as the SDK commit ships them.
            expected = {**copy.deepcopy(recorded), 'skills': {'backend_commit': revision, 'openapi_sha256': sha(api_bytes),
                                               'mcp_sha256': sha(mcp_bytes), 'tools_yaml_sha256': sha(yaml_bytes),
                                               'tools': tools['skills']}}
            output = {'x-contract-sources': expected, **self.swagger}
            for name in ('OPENAPI', 'MCP', 'MCP_CATALOG'):
                (root / checker[name]).write_bytes(b'dirty working tree')

            def sync(repo, script, *args):
                if script == 'sdkgen/sync_scoped_contract.py':
                    (repo / path).write_bytes(encode(output))

            command = Mock(side_effect=sync)
            with patch.dict(regenerate.__globals__, run_sdk=command, check_catalog_provenance=lambda *_: None):
                receipt = regenerate(root, sdk, revision)
                self.assertEqual([c.args for c in command.call_args_list], [
                    (sdk, 'sdkgen/sync_scoped_contract.py', root.resolve(), revision, head, '--scopes', 'skills'),
                    (sdk, 'sdkgen/generate.py', '--only', 'python', 'typescript'),
                    (sdk, 'sdkgen/generate.py', '--check', '--only', 'python', 'typescript'),
                    (sdk, 'sdkgen/check_skills_baseline.py', '--paired', head, '--product-commit', revision),
                ])
                self.assertEqual(receipt['sync_mode'], 'scoped')
                self.assertEqual(receipt['owned_scopes'], ['skills'])
                self.assertEqual(receipt['source'], expected)
                self.assertEqual(receipt['sha256'], {'swagger.json': sha(encode(output))})
                self.assertEqual(receipt['coverage']['skills_operations']['sdk'], 1)

                for scope, key in [('sql', 'backend_commit'), ('epoch', 'openapi_sha256'), ('skills', 'mcp_sha256'),
                                   ('sql', 'tools_yaml_sha256'), ('other', 'sdk_commit'), ('other', 'slice_sha256'),
                                   ('other', 'openapi_sha256'), ('skills', 'tools')]:
                    with self.subTest(scope=scope, key=key), patch.dict(expected[scope], {key: 'stale'}):
                        with self.assertRaisesRegex(ValueError, 'committed contract inputs'):
                            regenerate(root, sdk, revision)

                # A regenerated record that differs from the committed one is still refused.
                with patch.dict(expected['sql'], tools=['run_sql']):
                    with self.assertRaisesRegex(ValueError, 'committed contract inputs'):
                        regenerate(root, sdk, revision)
                    # SQL is pinned to what the SDK commit ships: product pairing never re-checks
                    # its inventory against the product (the SDK's own pairing does), so a
                    # product or SDK change there cannot fail a product build.
                    incomplete = {**recorded, 'sql': {**recorded['sql'], 'tools': ['run_sql']}}
                    commit(sdk, {path: encode({'x-contract-sources': incomplete})})
                    self.assertEqual(regenerate(root, sdk, revision)['source']['sql'], incomplete['sql'])
                    commit(sdk, {path: encode({'x-contract-sources': recorded})})

                # The committed slice is what shipped SDK clients call: the regenerated one may extend
                # it, but a product change that breaks it needs a paired SDK branch.
                commit(sdk, {path: encode({'x-contract-sources': recorded, **self.swagger})})
                regenerate(root, sdk, revision)
                legacy = {'name': 'legacy', 'in': 'query', 'required': True, 'type': 'string'}
                commit(sdk, {path: encode({'x-contract-sources': recorded,
                                           'paths': {self.path: {'get': {'parameters': [legacy]}}}})})
                with self.assertRaisesRegex(ValueError, "Breaking change.*required query input 'legacy' was removed"):
                    regenerate(root, sdk, revision)
                commit(sdk, {path: encode({'x-contract-sources': recorded})})

            # Malformed committed input cannot be repaired by overwriting it, or
            # bypassed by invoking the release sync when the sidecars are absent.
            broken_records = [None, {k: v for k, v in recorded.items() if k != 'skills'},
                              {**recorded, 'epoch': {**recorded['epoch'], 'mcp_sha256': '0' * 64}},
                              {**recorded, 'other': {**recorded['other'], 'slice_sha256': '0' * 64}},
                              {**recorded, **{k: {**recorded[k], 'backend_commit': revision} for k in tools}}]
            for snapshot in [{}, {'x-source-sha256': sha(api_bytes), 'x-contract-sources': recorded},
                             *({'x-contract-sources': r} for r in broken_records)]:
                commit(sdk, {path: encode(snapshot)})
                command.reset_mock()
                with patch.dict(regenerate.__globals__, run_sdk=command, check_catalog_provenance=lambda *_: None):
                    with self.assertRaises(ValueError):
                        regenerate(root, sdk, revision)
                command.assert_not_called()

    def test_breaking_skills_changes_name_what_a_shipped_client_breaks_on(self):
        breaking = checker['breaking_skills_changes']
        resolve, publish = '/model-hub/skills/resolve/', '/model-hub/skills/publish/'
        shipped = {
            'paths': {
                resolve: {'get': {'parameters': [{'name': 'name', 'in': 'query', 'required': True, 'type': 'string'},
                                                 {'name': 'label', 'in': 'query', 'type': 'string'}]}},
                publish: {'post': {'parameters': [{'name': 'data', 'in': 'body', 'required': True,
                                                   'schema': {'$ref': '#/definitions/Push'}}]}},
                '/model-hub/datasets/': {'get': {'parameters': []}},
            },
            'definitions': {'Push': {'type': 'object', 'required': ['name'],
                                     'properties': {'name': {'type': 'string'}, 'note': {'type': 'string'},
                                                    'metadata': {'type': 'object', 'additionalProperties': True},
                                                    'files': {'type': 'array', 'items': {'$ref': '#/definitions/File'}}}},
                            'File': {'type': 'object', 'required': ['path', 'content'],
                                     'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'},
                                                    'mode': {'type': 'string', 'enum': ['r', 'w'], 'x-nullable': True},
                                                    'id': {'type': 'string', 'readOnly': True}}}},
        }
        self.assertEqual(breaking(shipped, shipped), [])
        # An optional addition, and a non-Skills route the product drops, are not this check's concern.
        tolerated = copy.deepcopy(shipped)
        tolerated['paths'][resolve]['get']['parameters'].append({'name': 'extra', 'in': 'query', 'type': 'string'})
        tolerated['definitions']['Push']['properties']['tag'] = {'type': 'string'}
        del tolerated['paths']['/model-hub/datasets/']
        self.assertEqual(breaking(shipped, tolerated), [])
        cases = [
            (lambda d: d['paths'][resolve]['get']['parameters'][0].update(name='skill_name'),
             ["GET /model-hub/skills/resolve/: required query input 'name' was removed or renamed",
              "GET /model-hub/skills/resolve/: query input 'skill_name' is newly required"]),
            (lambda d: d['paths'][resolve]['get']['parameters'][1].update(required=True),
             ["GET /model-hub/skills/resolve/: query input 'label' is newly required"]),
            (lambda d: d['definitions']['Push'].update(required=['name', 'note']),
             ["POST /model-hub/skills/publish/: body input 'note' is newly required"]),
            (lambda d: d['definitions']['Push']['properties'].pop('name') and d['definitions']['Push'].update(required=[]),
             ["POST /model-hub/skills/publish/: required body input 'name' was removed or renamed"]),
            (lambda d: d['paths'].pop(publish), ['POST /model-hub/skills/publish/ is no longer served']),
            (lambda d: d['paths'][publish]['post']['parameters'][0].update(required=False), []),
            (lambda d: d['paths'][resolve]['get']['parameters'][0].update(type='integer'),
             ["GET /model-hub/skills/resolve/: query input 'name' changed type"]),
            (lambda d: d['paths'][resolve]['get']['parameters'][1].update(format='uuid'),
             ["GET /model-hub/skills/resolve/: query input 'label' changed format"]),
            (lambda d: d['definitions']['File']['properties']['mode'].update(enum=['r']),
             ["POST /model-hub/skills/publish/: body input 'files.[].mode' accepts fewer values"]),
            (lambda d: d['definitions']['File']['properties']['mode'].update(enum=['r', 'w', 'x']), []),
            (lambda d: d['definitions']['File']['properties']['mode'].pop('x-nullable'),
             ["POST /model-hub/skills/publish/: body input 'files.[].mode' no longer accepts null"]),
            # Fields one level or more down, behind a definition and an array, are guarded too.
            (lambda d: d['definitions']['File'].update(required=['content']), []),
            (lambda d: d['definitions']['File']['properties'].pop('path') and d['definitions']['File'].update(
                required=['content']),
             ["POST /model-hub/skills/publish/: required body input 'files.[].path' was removed or renamed"]),
            (lambda d: d['definitions']['File']['properties'].update(path={'type': 'string', 'readOnly': True}),
             ["POST /model-hub/skills/publish/: required body input 'files.[].path' was removed or renamed"]),
            (lambda d: d['definitions']['File'].update(required=['path', 'content', 'encoding']),
             ["POST /model-hub/skills/publish/: body input 'files.[].encoding' is newly required"]),
            # A required field of an object the shipped client never sends is not a break.
            (lambda d: d['definitions']['Push']['properties'].update(
                meta={'type': 'object', 'required': ['owner'], 'properties': {'owner': {'type': 'string'}}}), []),
            # Shipped callers send optional inputs too: removing or renaming one breaks them.
            (lambda d: d['paths'][resolve]['get']['parameters'].pop(1),
             ["GET /model-hub/skills/resolve/: optional query input 'label' was removed or renamed"]),
            (lambda d: d['definitions']['File']['properties'].pop('mode'),
             ["POST /model-hub/skills/publish/: optional body input 'files.[].mode' was removed or renamed"]),
            # Reported once, at the outermost input that is gone.
            (lambda d: d['definitions']['Push']['properties'].pop('files'),
             ["POST /model-hub/skills/publish/: optional body input 'files' was removed or renamed"]),
            # Required fields behind allOf branches and $ref aliases, at any depth, count.
            (lambda d: d['definitions'].update(
                File={'allOf': [{'$ref': '#/definitions/FileAlias'}]},
                FileAlias={'$ref': '#/definitions/FileBase'},
                FileBase={'allOf': [{'type': 'object', 'required': ['path'], 'properties': {'path': {'type': 'string'}}},
                                    {'required': ['content', 'encoding'], 'properties': {
                                        'content': {'type': 'string'}, 'encoding': {'type': 'string'},
                                        'mode': {'type': 'string', 'enum': ['r', 'w'], 'x-nullable': True}}}]}),
             ["POST /model-hub/skills/publish/: body input 'files.[].encoding' is newly required"]),
            # A free-form map that stops accepting arbitrary keys.
            (lambda d: d['definitions']['Push']['properties']['metadata'].update(additionalProperties={'type': 'string'}),
             ["POST /model-hub/skills/publish/: body input 'metadata' accepts fewer keys"]),
            (lambda d: d['definitions']['Push']['properties']['metadata'].update(additionalProperties=False),
             ["POST /model-hub/skills/publish/: body input 'metadata' accepts fewer keys"]),
            # A field whose definition is readOnly is response-only, not an input.
            (lambda d: d['definitions']['Push']['properties'].update(owner={'$ref': '#/definitions/Owner'})
             or d['definitions'].update(Owner={'type': 'string', 'readOnly': True})
             or d['definitions']['Push']['required'].append('owner'), []),
        ]
        # The same definition reached along two paths (an allOf branch and a property) is not a
        # cycle: replacing an inline copy with an equivalent $ref is compatible.
        base = {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'string'}}}
        composed = copy.deepcopy(shipped)
        composed['definitions'].update(Base=base, Push={'allOf': [{'$ref': '#/definitions/Base'}, {
            'type': 'object', 'properties': {'nested': copy.deepcopy(base)}}]})
        refactored = copy.deepcopy(composed)
        refactored['definitions']['Push']['allOf'][1]['properties']['nested'] = {'$ref': '#/definitions/Base'}
        self.assertEqual(breaking(composed, refactored), [])
        self.assertEqual(breaking(refactored, composed), [])
        inputs = checker['skills_inputs'](refactored, 'post', publish)
        self.assertEqual(inputs[('body', 'nested', 'value')][0], True)
        # A real cycle still terminates: a definition that contains itself.
        looped = copy.deepcopy(composed)
        looped['definitions']['Base']['properties']['child'] = {'$ref': '#/definitions/Base'}
        self.assertIn(('body', 'child'), checker['skills_inputs'](looped, 'post', publish))
        self.assertEqual(breaking(looped, looped), [])
        # Only operations the SDK exposes are guarded.
        product = copy.deepcopy(shipped)
        product['paths'].pop(publish)
        self.assertEqual(breaking(shipped, product, {('get', resolve)}), [])
        self.assertEqual(breaking(shipped, product, {('post', publish)}), ['POST /model-hub/skills/publish/ is no longer served'])
        for index, (change, expected) in enumerate(cases):
            product = copy.deepcopy(shipped)
            change(product)
            with self.subTest(case=index, expected=expected[:1]):
                self.assertEqual(breaking(shipped, product), expected)

    def test_one_sdk_exposure_rule(self):
        op = lambda path, surfaces: {'id': path, 'operation': {'method': 'GET', 'path': path}, 'surfaces': surfaces}
        catalog = {'operations': [op('/a/', {'sdk': {}}), op('/b/', {'sdk': {}, 'mcp': {'name': 'b'}}),
                                  op('/c/', {'mcp': {'name': 'c'}}), op('/d/', {'sdk': False, 'mcp': {'name': 'd'}})]}
        self.assertEqual(checker['sdk_exposed'](catalog), {('get', '/a/'), ('get', '/b/')})
        self.assertEqual([checker['sdk_surface'](e) for e in catalog['operations']], [True, True, False, False])

    def test_sql_tool_name_alone_does_not_prove_its_product_binding(self):
        endpoint = {'method': 'POST', 'path': '/tracer/sql-query/{id}/cancel/'}
        entry = {'id': 'sql.cancel', 'operation': endpoint, 'surfaces': {'mcp': {'name': 'cancel_sql'}}}
        sdk_catalog = {'operations': [entry, {**entry, 'id': 'sql.sdk_only', 'surfaces': {'mcp': False}}]}
        tool = {'name': 'cancel_sql', 'request': endpoint}
        shipped = checker['shipped_sql_tools']
        self.assertEqual(shipped(sdk_catalog, {'tools': [tool]}), ['cancel_sql'])
        for rows in ([], [{**tool, 'request': {**endpoint, 'method': 'GET'}}],
                     [{**tool, 'request': {**endpoint, 'path': '/tracer/sql-query/run/'}}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, 'bound differently.*cancel_sql'):
                shipped(sdk_catalog, {'tools': rows})

    def test_sdk_scripts_can_import_siblings_without_leaving_bytecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp)
            scripts = sdk / 'sdkgen'
            scripts.mkdir()
            (scripts / 'sdk_sibling.py').write_text('ok = True\n')
            (scripts / 'driver.py').write_text('from sdk_sibling import ok\nassert ok\n')
            checker['run_sdk'](sdk, 'sdkgen/driver.py')
            self.assertFalse((scripts / '__pycache__').exists())

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
