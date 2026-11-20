"""Controls for branch matching and the regenerated Skills contract."""

import copy
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
        self.catalog = {'tools': [{'name': 'list_skills', 'request': {'method': 'GET', 'path': self.path}}]}
        self.entry = {'id': 'skills.list', 'operation': {'method': 'GET', 'path': self.path},
                      'surfaces': {'sdk': {}, 'mcp': {'name': 'list_skills'}}}

    def check(self):
        return check_slice(self.api, self.swagger, self.catalog, {'operations': [self.entry]})

    def test_unwrapped_backend_operations_are_reported_not_fatal(self):
        self.assertEqual(self.check()['skills_operations']['not_wrapped'], [])
        self.api['paths'][self.path]['post'] = {}
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

    def test_snapshot_alone_is_not_a_shipped_surface(self):
        self.entry['surfaces']['sdk'] = False
        with self.assertRaisesRegex(ValueError, 'ships no Skills operations'):
            self.check()
        self.entry['surfaces']['sdk'] = {}
        self.catalog['tools'][0]['request']['method'] = 'POST'
        with self.assertRaisesRegex(ValueError, 'bound differently'):
            self.check()

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
