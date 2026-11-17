"""Offline persistence tests for landing_fixture.py; no app/server/provider boot.

Uses the production Workspace/WorkspaceMembership and BaseModel with SQLite.
Only unrelated account/SAML tables and audit signals are isolated stand-ins.
This proves fixture persistence, not product login or PostgreSQL integration.
Run in an isolated process: python -I e2e/harness/test_landing_fixture.py.
"""
import contextlib
from importlib.machinery import ModuleSpec
from pathlib import Path
import socket
import sys
import types
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def refuse_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        raise RuntimeError('landing fixture tests are offline')


sys.addaudithook(refuse_network)
sys.path.insert(0, str(ROOT / 'futureagi'))

# Avoid accounts.__init__/AppConfig.ready and their unrelated application boot.
accounts = types.ModuleType('accounts')
accounts.__path__ = [str(ROOT / 'futureagi/accounts')]
sys.modules['accounts'] = accounts
account_models = types.ModuleType('accounts.models')
account_models.__spec__ = ModuleSpec('accounts.models', loader=None, is_package=True)
account_models.__path__ = [str(ROOT / 'futureagi/accounts/models')]
sys.modules['accounts.models'] = account_models
from django.apps import AppConfig
from django.conf import settings


class OfflineAccounts(AppConfig):
    name = 'accounts'
    path = str(ROOT / 'futureagi/accounts')


settings.configure(INSTALLED_APPS=[OfflineAccounts('accounts', accounts)], USE_TZ=True,
                   DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
import django
django.setup()
from django.db import connection, models
from tfc.utils.base_model import BaseModel
from tfc.constants.roles import OrganizationRoles


class User(BaseModel):
    __module__ = 'accounts.models'
    id = models.UUIDField(primary_key=True, default=uuid4)
    email = models.CharField(max_length=255)
    config = models.JSONField(default=dict)


class Organization(BaseModel):
    __module__ = 'accounts.models'
    id = models.UUIDField(primary_key=True, default=uuid4)
    name = models.CharField(max_length=255, default='offline fixture')


class OrganizationMembership(BaseModel):
    __module__ = 'accounts.models'
    id = models.UUIDField(primary_key=True, default=uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    role = models.CharField(max_length=30)
    level = models.IntegerField(default=3)
    is_active = models.BooleanField(default=True)


class SAMLMetadataModel(BaseModel):
    __module__ = 'accounts.models'
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)


# The fixture now imports these account dependencies too. Keep them explicit
# stand-ins, never boot the production auth app just to test workspace persistence.
# Pending-invite/auth/cache behavior is proved by the real-stack G3 controls, not
# by these minimal tables. Workspace/WorkspaceMembership remain production code.
class OrgApiKey(BaseModel):
    __module__ = 'accounts.models'
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey(User, null=True, on_delete=models.CASCADE)
    type = models.CharField(max_length=20, default='user')


class AuthToken(BaseModel):
    __module__ = 'accounts.models'
    user = models.ForeignKey(User, on_delete=models.CASCADE)


class OrganizationInvite(BaseModel):
    __module__ = 'accounts.models'
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)


class AuditLog(BaseModel):
    __module__ = 'accounts.models'
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result


account_models.User = User
module('accounts.models.organization', Organization=Organization)
module('accounts.models.organization_membership', OrganizationMembership=OrganizationMembership)
module('accounts.models.user', OrgApiKey=OrgApiKey)
module('accounts.models.auth_token', AuthToken=AuthToken)
module('accounts.models.organization_invite', OrganizationInvite=OrganizationInvite,
       InviteStatus=types.SimpleNamespace(PENDING='Pending', EXPIRED='Expired', CANCELLED='Cancelled'))
module('accounts.models.audit_log', AuditLog=AuditLog)
module('saml2_auth.models', SAMLMetadataModel=SAMLMetadataModel)
module('tfc.utils.audit', mute_audit_signals=contextlib.nullcontext)
from accounts.models.workspace import Workspace, WorkspaceMembership

MODELS = [User, Organization, OrganizationMembership, Workspace, WorkspaceMembership, SAMLMetadataModel,
          OrgApiKey, AuthToken, OrganizationInvite, AuditLog]
with connection.schema_editor() as editor:
    for model in MODELS:
        editor.create_model(model)

# Execute the complete seed/dispose function, only removing its CLI entrypoint.
source = (ROOT / 'e2e/lib/landing_fixture.py').read_text()
entrypoint = "print('E2E-LANDING ' + json.dumps(apply(json.load(sys.stdin))))"
if source.count(entrypoint) != 1:
    raise RuntimeError('landing fixture entrypoint changed')
namespace = {}
exec(compile(source.replace(entrypoint, ''), 'landing_fixture.py', 'exec'), namespace)
apply = namespace['apply']


class LandingPersistence(unittest.TestCase):
    def setUp(self):
        for model in reversed(MODELS):
            model.all_objects.all().delete()
        self.owner = User.objects.create(email='e2e-owner@fixture.invalid')
        self.other = User.objects.create(email='e2e-other@fixture.invalid')
        self.actor = User.objects.create(email='e2e-member@fixture.invalid', config={'retained': True})
        self.org, self.foreign = Organization.objects.create(), Organization.objects.create()
        for user, org, role in [(self.owner, self.org, OrganizationRoles.OWNER),
                                (self.other, self.foreign, OrganizationRoles.OWNER),
                                (self.actor, self.org, OrganizationRoles.MEMBER)]:
            OrganizationMembership.objects.create(user=user, organization=org, role=role)
        self.membership = OrganizationMembership.objects.get(user=self.actor)
        self.original = Workspace.objects.create(organization=self.org, created_by=self.owner,
                                                  name='existing', is_default=True)
        WorkspaceMembership.objects.create(user=self.actor, workspace=self.original,
                                             organization_membership=self.membership)

    def args(self, scenario):
        ids = ['ffffffff-1111-4111-8111-111111111111',
               '11111111-1111-4111-8111-111111111111',
               'ffffffff-2222-4222-8222-222222222222']
        return {'action': 'seed', 'scenario': scenario, 'nonce': str(uuid4()),
                'shape': 'legacy-pair', 'localWorkspaceIds': ids[:2], 'foreignWorkspaceId': ids[2],
                'wrongExpectedWorkspaceId': ids[2], 'foreignMembershipExpected': False,
                'expectedActiveMembershipIdsBeforeAuth': ids[:2], 'expectedActiveMembershipIdsAfterAuth': ids[:2],
                'expected': ids[1 if scenario == 'reversed-uuid' else 0],
                'selectors': dict(saved=None, current=None, configured=None),
                'candidates': dict(saved=None, current=None, configured=None, isDefault=None,
                                   membershipOrder=ids[1 if scenario == 'reversed-uuid' else 0]),
                'membershipCreatedAt': {w: '2020-01-01T00:00:00.12345' +
                    str(6 + (i if scenario == 'membership-order' else 0)) + '+00:00' for i, w in enumerate(ids[:2])},
                'org': str(self.org.pk), 'foreignOrg': str(self.foreign.pk),
                'owner': str(self.owner.pk), 'foreignOwner': str(self.other.pk),
                'actor': str(self.actor.pk), 'noMembership': False, 'preference': None, 'samlOrgId': None,
                'workspaces': [{'id': value, 'org': str(self.org.pk if i < 2 else self.foreign.pk),
                                'is_default': i == 2} for i, value in enumerate(ids)]}

    def rows(self):
        # Fetch fresh database rows; never inspect objects returned by create().
        return list(WorkspaceMembership.objects.filter(user=self.actor)
                    .order_by('created_at', 'workspace_id').values('workspace_id', 'created_at'))

    def test_reversed_uuid_is_an_exact_persisted_microsecond_tie(self):
        args = self.args('reversed-uuid')
        apply(args)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['created_at'], rows[1]['created_at'], 'UUID case must tie exactly')
        self.assertEqual(rows[0]['created_at'].microsecond, 123456)
        self.assertEqual(str(rows[0]['workspace_id']), args['workspaces'][1]['id'])
        self.assertFalse(Workspace.objects.filter(organization=self.org, is_default=True).exists())

    def test_membership_order_is_separate_and_beats_uuid(self):
        args = self.args('membership-order')
        apply(args)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]['created_at'], rows[1]['created_at'])
        self.assertEqual((rows[1]['created_at'] - rows[0]['created_at']).total_seconds(), 0.000001)
        self.assertGreater(rows[0]['workspace_id'], rows[1]['workspace_id'])
        self.assertEqual(str(rows[0]['workspace_id']), args['workspaces'][0]['id'])
        self.assertFalse(Workspace.objects.filter(organization=self.org, is_default=True).exists())

    def test_timestamp_updates_do_not_drift_restored_memberships(self):
        before = list(WorkspaceMembership.objects.filter(user=self.actor).values())
        for scenario in ('reversed-uuid', 'membership-order'):
            args = self.args(scenario)
            undo = apply(args)
            apply({**args, **undo, 'action': 'dispose'})
            self.assertEqual(list(WorkspaceMembership.objects.filter(user=self.actor).values()), before)
            self.original.refresh_from_db()
            self.actor.refresh_from_db()
            self.assertTrue(self.original.is_default)
            self.assertEqual(self.actor.config, {'retained': True})

    def test_v2_arguments_preserve_the_legacy_pair_contract(self):
        for scenario in ('reversed-uuid', 'membership-order'):
            args = self.args(scenario)
            self.assertEqual(args['shape'], 'legacy-pair')
            ids = [row['id'] for row in args['workspaces']]
            self.assertEqual(args['localWorkspaceIds'], ids[:2])
            self.assertEqual(args['foreignWorkspaceId'], ids[2])
            self.assertEqual(args['wrongExpectedWorkspaceId'], ids[2])
            self.assertEqual(args['expectedActiveMembershipIdsBeforeAuth'], ids[:2])
            self.assertEqual(args['expectedActiveMembershipIdsAfterAuth'], ids[:2])
            self.assertEqual(args['selectors'], dict(saved=None, current=None, configured=None))
            self.assertFalse(args['foreignMembershipExpected'])
            self.assertEqual(args['expected'], ids[1 if scenario == 'reversed-uuid' else 0])
            self.assertEqual(namespace['validate_shape'](args), args['workspaces'])

    def test_new_dependencies_stay_isolated_but_workspace_models_are_real(self):
        self.assertEqual(namespace['Workspace'].__module__, 'accounts.models.workspace')
        self.assertEqual(namespace['WorkspaceMembership'].__module__, 'accounts.models.workspace')
        for name in ('User', 'OrgApiKey', 'AuthToken', 'OrganizationInvite', 'AuditLog'):
            self.assertEqual(namespace[name].__module__, 'accounts.models')
        self.assertNotIn('accounts.authentication', sys.modules)
        self.assertNotIn('django.contrib.auth.models', sys.modules)

    def test_network_refused_before_connect(self):
        with socket.socket() as sock:
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                sock.connect(('192.0.2.1', 443))


if __name__ == '__main__':
    unittest.main(verbosity=2)
