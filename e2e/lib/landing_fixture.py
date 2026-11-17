# Input to manage.py shell, not an application module. Synthetic test data only.
import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5
from hashlib import sha256
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from accounts.models.user import OrgApiKey
from accounts.models.auth_token import AuthToken
from accounts.models.organization_invite import OrganizationInvite, InviteStatus
from tfc.constants.levels import INVITE_VALIDITY_DAYS
from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from accounts.models.audit_log import AuditLog
from accounts.models import User
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.roles import OrganizationRoles
from tfc.utils.audit import mute_audit_signals

class SnapshotEncoder(DjangoJSONEncoder):
    def default(self, value):
        # Preserve all microseconds (Django's default truncates to milliseconds).
        if isinstance(value, datetime):
            return value.isoformat()
        return super().default(value)

def require(condition, message):
    # These are mutation safety boundaries, including when Python runs with -O.
    if not condition:
        raise ValueError(message)

def digest(value):
    return sha256(json.dumps(value, cls=SnapshotEncoder, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def rows_digest(query):
    return digest(list(query.order_by('pk').values()))

def scope_users(scopes, actor=None):
    # Unfiltered preexisting tenant users plus actor, never just the invitee.
    return User._base_manager.filter(Q(organization_id__in=scopes) |
        Q(organization_memberships__organization_id__in=scopes) |
        Q(workspace_memberships__workspace__organization_id__in=scopes) |
        Q(invited_organizations__pk__in=scopes) | Q(pk=actor.pk if actor else None)).distinct()

def all_rows(scopes, actor=None, exclude_workspace_ids=()):
    users = scope_users(scopes, actor)
    actor_q = Q(user=actor) if actor else Q(pk__in=[])
    return {
        'organizations': rows_digest(Organization._base_manager.filter(pk__in=scopes)),
        'audit': rows_digest(AuditLog._base_manager.filter(organization_id__in=scopes)),
        'users': rows_digest(users),
        'workspaces': rows_digest(Workspace._base_manager.filter(organization_id__in=scopes).exclude(pk__in=exclude_workspace_ids)),
        'memberships': rows_digest(WorkspaceMembership._base_manager.filter(Q(workspace__organization_id__in=scopes) | actor_q)),
        'orgMemberships': rows_digest(OrganizationMembership._base_manager.filter(Q(organization_id__in=scopes) | actor_q)),
        'invites': rows_digest(OrganizationInvite._base_manager.filter(organization_id__in=scopes)),
        'invitedOrgs': rows_digest(User.invited_organizations.through.objects.filter(organization_id__in=scopes)),
        'tokens': rows_digest(AuthToken._base_manager.filter(user__in=users)),
        'keys': rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes)),
    }

def cache_state():
    # Actual isolated cache including new keys; absolute expiry, not elapsed TTL.
    location = settings.CACHES['default'].get('LOCATION', '')
    require(isinstance(location, str) and urlsplit(location).hostname in ('localhost', '127.0.0.1', '::1'),
            'J02 requires a qualified loopback Redis cache')
    backend = settings.CACHES['default']['BACKEND']
    require(backend in ('django_redis.cache.RedisCache', 'django.core.cache.backends.redis.RedisCache'),
            'J02 requires the real Redis cache, not locmem')
    client = cache.client.get_client(write=False) if backend.startswith('django_redis.') else cache._cache.get_client(write=False)
    keys = []
    for key in client.scan_iter(match=cache.make_key('*'), count=100):
        keys.append(key)
        require(len(keys) <= 10000, 'J02 cache snapshot bound exceeded')
    rows = []
    for key in sorted(set(keys)):
        value = client.dump(key)
        require(value is not None, 'J02 cache changed during read')
        rows.append((sha256(key).hexdigest(), sha256(value).hexdigest(), client.execute_command('PEXPIRETIME', key)))
    return digest(rows)

def validate_shape(args):
    require(args.get('shape') in ('legacy-pair', 'precedence-ladder', 'pending-invite'), 'unknown landing shape')
    require((args['scenario'] in ('saved-preference', 'legacy-current', 'configured-default')) ==
            (args['shape'] == 'precedence-ladder'), 'case and shape disagree')
    require((args['scenario'] == 'pending-invite') == (args['shape'] == 'pending-invite'), 'invite shape mismatch')
    rows = args['workspaces']
    ladder = args.get('shape') == 'precedence-ladder'
    count = 5 if ladder else 2
    require(len(rows) == count + 1 and [r['org'] for r in rows] == [args['org']] * count + [args['foreignOrg']],
            'landing workspace scopes do not match')
    ids = [r['id'] for r in rows]
    require(len(set(ids)) == count + 1, 'landing workspaces must be distinct')
    for value in ids:
        UUID(value)
    require(args['localWorkspaceIds'] == ids[:-1] and args['foreignWorkspaceId'] == ids[-1]
            and args['wrongExpectedWorkspaceId'] == ids[-1] and args['expected'] != ids[-1], 'foreign landing refused')
    require(args['expected'] is None or args['expected'] in ids[:-1], 'unknown landing winner')
    if ladder:
        roles = args['candidates']
        names = ['saved', 'current', 'configured', 'isDefault', 'membershipOrder']
        require([roles[n] for n in names] == ids[:-1], 'ladder role ownership mismatch')
        scenario = args['scenario']
        require(scenario in ('saved-preference', 'legacy-current', 'configured-default'), 'unknown ladder')
        selectors = {'saved': roles['saved'] if scenario == 'saved-preference' else None,
                     'current': roles['current'] if scenario != 'configured-default' else None, 'configured': roles['configured']}
        require(args['selectors'] == selectors and args['expected'] == next(v for v in selectors.values() if v), 'ladder selectors mismatch')
        require([r['id'] for r in rows[:-1] if r['is_default']] == [roles['isDefault']], 'ladder default mismatch')
        require(args['expectedActiveMembershipIdsBeforeAuth'] == ids[:-1] == args['expectedActiveMembershipIdsAfterAuth']
                and not args['noMembership'] and not args['foreignMembershipExpected'], 'ladder grants mismatch')
        times = args['membershipCreatedAt']
        require(times == {w: '2020-01-01T00:00:00.12345' + str(0 if w == roles['membershipOrder'] else i + 1) + '+00:00'
                          for i, w in enumerate(ids[:-1])}, 'ladder timestamps mismatch')
        require(roles['membershipOrder'] != min(ids[:-1]), 'ladder UUID must not decide membership order')
    if args.get('shape') == 'pending-invite':
        require(rows[0]['is_default'] is True and rows[1]['is_default'] is False and ids[0] > ids[1]
                and args['expected'] == ids[0] and args['noMembership'] and not args['foreignMembershipExpected']
                and args['expectedActiveMembershipIdsBeforeAuth'] == []
                and args['expectedActiveMembershipIdsAfterAuth'] == ids[:-1]
                and args['selectors'] == dict(saved=None, current=None, configured=None), 'pending plan mismatch')
    return rows

def apply(args):
    if args.get('shape') == 'pending-invite':
        return pending_invite(args)
    require(args['action'] in ('seed', 'dispose', 'snapshot', 'inject-inactive', 'remove-inactive'), 'unknown landing action')
    require(args['scenario'] in (
        'reversed-uuid', 'membership-order', 'is-default', 'stale-preference', 'cross-org-preference',
        'saml-scoped-default', 'owner-without-membership', 'member-without-membership',
        'saved-preference', 'legacy-current', 'configured-default', 'inactive-preference',
    ), 'unknown landing case')
    require(args['org'] != args['foreignOrg'], 'landing scopes must be distinct')
    UUID(args['nonce'])
    scopes = [args['org'], args['foreignOrg']]
    rows = validate_shape(args)
    # Data-fixture teardown must not emit synthetic member.removed audit rows.
    with transaction.atomic(), mute_audit_signals():
        list(Organization.objects.select_for_update().filter(pk__in=scopes).order_by('pk'))
        owner = User.objects.get(pk=args['owner'], email__startswith='e2e-')
        other = User.objects.get(pk=args['foreignOwner'], email__startswith='e2e-')
        actor = User.objects.select_for_update().get(pk=args['actor'], email__startswith='e2e-')
        for user, org in [(owner, args['org']), (other, args['foreignOrg'])]:
            require(OrganizationMembership.no_workspace_objects.filter(
                user=user, organization_id=org, role=OrganizationRoles.OWNER,
                is_active=True, deleted=False).exists(), 'not the owned test org')
        membership = OrganizationMembership.no_workspace_objects.get(
            user=actor, organization_id=args['org'], is_active=True, deleted=False)
        expected_role = OrganizationRoles.OWNER if args['scenario'] == 'owner-without-membership' else OrganizationRoles.MEMBER
        require(membership.role == expected_role, 'actor role does not match case')
        require(not OrganizationMembership._base_manager.filter(
            organization_id__in=scopes).exclude(user__email__startswith='e2e-').exists(),
            'landing org contains a non-synthetic user')
        if args['action'] == 'snapshot':
            categories = all_rows(scopes, actor)
            return {'categories': categories, 'protectedDigest': digest(categories)}
        if args['action'] in ('inject-inactive', 'remove-inactive'):
            # A11: the fixture chooses the row/target, never a caller-supplied PK.
            require(args['scenario'] == 'member-without-membership' and membership.level == 3,
                    'inactive fault requires ordinary member without memberships')
            workspace = Workspace._base_manager.get(pk=args['localWorkspaceIds'][0], organization_id=args['org'],
                created_by=owner, name=f"e2e-landing-{args['nonce']}-0", is_active=True, deleted=False)
            all_grants = WorkspaceMembership._base_manager.filter(user=actor)
            fault_id = uuid5(UUID(args['nonce']), 'j02-a11-inactive-membership')
            fault_row = all_grants.filter(pk=fault_id, workspace=workspace, organization_membership=membership,
                is_active=False, deleted=False, role=OrganizationRoles.WORKSPACE_MEMBER, level=3)
            if args['action'] == 'inject-inactive':
                require(not all_grants.exists(), 'inactive fault requires empty all-row baseline')
                baseline = rows_digest(all_grants)
                WorkspaceMembership.no_workspace_objects.create(id=fault_id, user=actor, workspace=workspace,
                    organization_membership=membership, role=OrganizationRoles.WORKSPACE_MEMBER, level=3, is_active=False)
                require(fault_row.count() == 1, 'inactive fault insertion failed')
                return {'rowDigest': rows_digest(fault_row), 'baseline': baseline}
            receipt = args['fault']
            require(fault_row.count() == 1 and rows_digest(fault_row) == receipt['rowDigest'], 'owned inactive fault changed')
            fault_row.delete()
            require(rows_digest(all_grants) == receipt['baseline'], 'inactive fault restore mismatch')
            return {}
        workspaces = Workspace._base_manager.filter(organization_id__in=scopes)
        actor_memberships = WorkspaceMembership._base_manager.filter(
            user=actor, workspace__organization_id=args['org'])
        saml_name = f"e2e-landing-{args['nonce']}"
        if args['action'] == 'dispose':
            before = json.loads(args['undo'])
            # QuerySet.delete is a hard delete; remove only this fixture's rows.
            Workspace._base_manager.filter(id__in=[row['id'] for row in rows],
                                         organization_id__in=scopes).delete()
            if before['foreign_membership']:
                OrganizationMembership._base_manager.filter(
                    pk=before['foreign_membership'], user=actor,
                    organization_id=args['foreignOrg']).delete()
            if args['scenario'] == 'saml-scoped-default':
                SAMLMetadataModel._base_manager.filter(
                    organization_id=args['org'], name=saml_name).delete()
            for row in before['defaults']:
                workspaces.filter(pk=row['id']).update(is_default=row['is_default'])
            # Raw save_base preserves PKs/times; bulk_create re-runs auto_now_add.
            for item in serializers.deserialize('json', before['memberships']):
                item.save()
            User.objects.filter(pk=actor.pk).update(config=before['config'])
            return {}
        require(not OrgApiKey._base_manager.filter(organization_id__in=scopes).filter(
            ~Q(type='user') | Q(user__isnull=True)).exists(), '733 person-only keys required')
        if args['scenario'] == 'saml-scoped-default':
            require(not SAMLMetadataModel._base_manager.filter(organization_id=args['org']).exists(),
                    'landing SAML needs an unconfigured owned org')
        keys_before = rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes))
        defaults_scope = workspaces.filter(organization_id=args['org']) if args['scenario'] in (
            'saved-preference', 'legacy-current', 'configured-default', 'inactive-preference') else workspaces
        before = {
            'defaults': [{'id': str(row.pk), 'is_default': row.is_default} for row in defaults_scope],
            'memberships': serializers.serialize('json', actor_memberships, cls=SnapshotEncoder),
            'config': actor.config,
            'foreign_membership': None,
        }
        defaults_scope.update(is_default=False)
        created = []
        for index, row in enumerate(rows):
            creator = owner if row['org'] == args['org'] else other
            workspace = Workspace.no_workspace_objects.create(
                id=row['id'], name=f"{saml_name}-{index}", organization_id=row['org'],
                created_by=creator, is_default=row['is_default'], is_active=row.get('is_active', True))
            created.append(workspace)
        actor_memberships.delete()
        if not args['noMembership']:
            grants = []
            for workspace in (w for w in created if str(w.pk) in args['localWorkspaceIds']):
                grants.append(WorkspaceMembership.no_workspace_objects.create(user=actor, workspace=workspace,
                    organization_membership=membership, role=OrganizationRoles.WORKSPACE_MEMBER, level=3))
            # Override auto_now_add AFTER creation: UUID case ties exactly;
            # membership-order makes high UUID earlier by one microsecond.
            stamp = datetime(2020, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc)
            if args['scenario'] == 'reversed-uuid':
                WorkspaceMembership.no_workspace_objects.filter(pk__in=[g.pk for g in grants]).update(created_at=stamp)
            elif args['scenario'] == 'membership-order':
                for index, grant in enumerate(grants):
                    WorkspaceMembership.no_workspace_objects.filter(pk=grant.pk).update(
                        created_at=stamp + timedelta(microseconds=index))
            if args['shape'] == 'precedence-ladder':
                for grant in grants:
                    WorkspaceMembership._base_manager.filter(pk=grant.pk).update(
                        created_at=datetime.fromisoformat(args['membershipCreatedAt'][str(grant.workspace_id)]))
        if args['scenario'] == 'saml-scoped-default':
            foreign_membership, added = OrganizationMembership.no_workspace_objects.get_or_create(
                user=actor, organization_id=args['foreignOrg'], defaults={
                    'role': OrganizationRoles.MEMBER, 'level': 3, 'is_active': True})
            if added:
                before['foreign_membership'] = str(foreign_membership.pk)
            WorkspaceMembership.no_workspace_objects.create(user=actor, workspace=next(w for w in created if str(w.pk) == args['foreignWorkspaceId']),
                organization_membership=foreign_membership, role=OrganizationRoles.WORKSPACE_MEMBER, level=3)
        config = dict(actor.config or {})
        for key, role in [('currentWorkspaceId', 'current'), ('defaultWorkspaceId', 'configured')]:
            config.pop(key, None)
            if args['selectors'][role]:
                config[key] = args['selectors'][role]
        saved = args['selectors']['saved']
        config['orgWorkspaceMap'] = {args['org']: saved} if saved else {}
        User.objects.filter(pk=actor.pk).update(config=config)
        require(rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes)) == keys_before, 'automatic key mint refused')
        return {'undo': json.dumps(before)}

def pending_invite(args):
    action = args['action']
    require(action in ('prepare-invite', 'seal-issuer-auth', 'begin-owner-readback', 'seal-owner-readback',
                       'inspect-invite', 'expire-invite', 'status-invite', 'remove-invite', 'dispose-invite'), 'unknown invite action')
    require(args['scenario'] == 'pending-invite' and args['org'] != args['foreignOrg'], 'invalid invite shape')
    UUID(args['nonce'])
    require(args['email'] == f"e2e-{args['nonce']}-invite@example.invalid", 'invalid synthetic invitee')
    rows = validate_shape(args)
    scopes = [args['org'], args['foreignOrg']]
    with transaction.atomic(), mute_audit_signals():
        list(Organization.objects.select_for_update().filter(pk__in=scopes).order_by('pk'))
        owners = [User.objects.get(pk=args[key], email__startswith='e2e-') for key in ('owner', 'foreignOwner')]
        for owner, org in zip(owners, scopes):
            require(OrganizationMembership._base_manager.filter(user=owner, organization_id=org,
                    role=OrganizationRoles.OWNER, is_active=True, deleted=False).exists(), 'not the owned test org')
        require(not OrganizationMembership._base_manager.filter(organization_id__in=scopes)
                .exclude(user__email__startswith='e2e-').exists(), 'invite org contains non-synthetic member')
        user = User.objects.filter(email=args['email']).first()
        invites = OrganizationInvite._base_manager.filter(target_email=args['email'], organization_id=args['org'])
        workspaces = Workspace._base_manager.filter(organization_id__in=scopes)
        system_keys = OrgApiKey._base_manager.filter(organization_id__in=scopes).exclude(type='user')
        unowned_keys = OrgApiKey._base_manager.filter(organization_id__in=scopes, user__isnull=True)
        if action == 'prepare-invite':
            require(not user and not OrganizationInvite._base_manager.filter(target_email=args['email']).exists(), 'invite identity is not fresh')
            require(not system_keys.exists() and not unowned_keys.exists(), '733 person-only keys required')
            require(settings.EMAIL_BACKEND == 'local_mail_backend.EmailBackend'
                    and getattr(settings, 'E2E_MAIL_CAPTURE_SOCKET', '') == args['socketPath'], 'mail backend binding mismatch')
            require(settings.APP_BASE_URL == args['appOrigin'] and settings.APP_URL == args['appOrigin'], 'mail app origin mismatch')
            require(urlsplit(args['appOrigin']).hostname in ('localhost', '127.0.0.1', '::1'), 'nonlocal app origin')
            cache_state()  # Fail before any mutation if the real cache reader is unavailable.
            # Bind the exact encrypted synthetic bearer without decode_token's writes.
            from accounts.authentication import decrypt_message
            credential = decrypt_message(args['issuerAccess'])
            require(str(credential.get('user_id')) == str(owners[0].pk), 'issuer bearer owner mismatch')
            issuer = AuthToken._base_manager.get(pk=credential['id'], user=owners[0], auth_type='access', is_active=True, deleted=False)
            require(issuer.auth_origin != 'saml' and issuer.last_used_at is not None, 'ordinary live issuer required')
            before = {'issuerAuth': {'id': str(issuer.pk), 'usedAt': issuer.last_used_at, 'startedAt': datetime.now(timezone.utc)},
                      'defaults': list(workspaces.filter(organization_id=args['org']).values('id', 'is_default')),
                      'workspaceDigest': rows_digest(workspaces),
                      'keys': rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes)),
                      'foreign': all_rows([args['foreignOrg']]),
                      'local': all_rows([args['org']])}
            workspaces.filter(organization_id=args['org']).update(is_default=False)
            for i, row in enumerate(rows):
                Workspace.no_workspace_objects.create(id=row['id'], organization_id=row['org'],
                    name=f"e2e-landing-{args['nonce']}-{i}", created_by=owners[0 if row['org'] == args['org'] else 1],
                    is_default=row['is_default'])
            require(rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes)) == before['keys'], 'automatic key mint refused')
            return {'undo': json.dumps(before, cls=SnapshotEncoder)}
        if action == 'seal-issuer-auth':
            # 0f047cc94a authentication.py:945-1005,1068-1076: only issuer last_used_at.
            # Seal ONCE after issuance, never during inspection/disposal or by DB repair.
            before = json.loads(args['undo'])
            require('issuerTokensAfter' not in before, 'issuer bookkeeping already sealed')
            tokens = list(AuthToken._base_manager.filter(user__in=scope_users([args['org']])).order_by('pk').values())
            issuer = [t for t in tokens if str(t['id']) == before['issuerAuth']['id']]
            require(len(issuer) == 1, 'issuer token missing')
            prior = datetime.fromisoformat(before['issuerAuth']['usedAt'])
            touched = issuer[0]['last_used_at']
            if args['issuerSucceeded'] or touched != prior:
                require(touched is not None and prior < touched and
                        datetime.fromisoformat(before['issuerAuth']['startedAt']) <= touched <= datetime.now(timezone.utc),
                        'issuer touch outside request interval')
            after = digest(tokens)
            issuer[0]['last_used_at'] = prior  # In-memory comparison ONLY; never save.
            require(digest(tokens) == before['local']['tokens'], 'issuer changed protected token state')
            before['issuerTokensAfter'] = after  # All rows/fields sealed, including the touch.
            return {'undo': json.dumps(before)}
        if action == 'dispose-invite':
            before = json.loads(args['undo'])
            if user:
                require(str(user.organization_id) == args['org'] and user.invited_by_id == owners[0].pk,
                        'refuse unowned invite cleanup')
                for token in AuthToken._base_manager.filter(user=user):
                    cache.delete(f'{token.auth_type}_token_{token.pk}')
                User.objects.filter(pk=user.pk).delete()
            # Include the public issuance audit even after a missing-authority control.
            AuditLog._base_manager.filter(organization_id=args['org']).filter(
                Q(target_id__in=list(invites.values_list('pk', flat=True))) |
                Q(action='member.invited', actor=owners[0], changes__email=args['email'])).delete()
            invites.delete()
            workspaces.filter(pk__in=[r['id'] for r in rows]).delete()
            for row in before['defaults']:
                workspaces.filter(pk=row['id']).update(is_default=row['is_default'])
            require(rows_digest(workspaces) == before['workspaceDigest'], 'workspace restore mismatch')
            require(all_rows([args['foreignOrg']]) == before['foreign'], 'foreign victim changed')
            # Never heal/rebaseline at disposal. Only individually bracketed issuer
            # and owner-readback touches were sealed, with all other token fields exact.
            expected = {**before['local'], 'tokens': before.get('issuerTokensAfter', before['local']['tokens'])}
            require(all_rows([args['org']]) == expected, 'preexisting local rows changed')
            return {}
        require(user is not None and str(user.organization_id) == args['org'] and user.invited_by_id == owners[0].pk,
                'pending invitee ownership mismatch')
        invite = invites.first()
        if action in ('begin-owner-readback', 'seal-owner-readback'):
            # Ordinary owner GETs use the same JWT sliding-touch path as issuance
            # (0f047cc94a authentication.py:945-1005,1068-1076). Bracket ONLY the
            # fixed public roster/member GETs; never settle drift at disposal.
            before = json.loads(args['undo'])
            require('issuerTokensAfter' in before, 'issuer bookkeeping not sealed')
            preexisting = AuthToken._base_manager.filter(user__in=scope_users([args['org']]).exclude(pk=user.pk))
            if action == 'begin-owner-readback':
                require(rows_digest(preexisting) == before['issuerTokensAfter'], 'pre-readback token drift')
                workspace_id = args.get('readWorkspace')
                if workspace_id is not None:
                    require(str(UUID(workspace_id)) == workspace_id and workspaces.filter(pk=workspace_id,
                            organization_id=args['org'], is_active=True, deleted=False).exists(), 'nonlocal owner readback')
                issuer = preexisting.get(pk=before['issuerAuth']['id'], user=owners[0])
                receipt = {'usedAt': issuer.last_used_at, 'startedAt': datetime.now(timezone.utc),
                           'categories': all_rows(scopes, user)}
                return {'readback': json.dumps(receipt, cls=SnapshotEncoder)}
            receipt = json.loads(args['readback'])
            tokens = list(AuthToken._base_manager.filter(user__in=scope_users(scopes, user)).order_by('pk').values())
            issuer = [t for t in tokens if str(t['id']) == before['issuerAuth']['id'] and t['user_id'] == owners[0].pk]
            require(len(issuer) == 1, 'readback issuer token missing')
            prior = datetime.fromisoformat(receipt['usedAt'])
            touched = issuer[0]['last_used_at']
            if args['readSucceeded'] or touched != prior:
                require(touched is not None and prior < touched and
                        datetime.fromisoformat(receipt['startedAt']) <= touched <= datetime.now(timezone.utc),
                        'readback touch outside request interval')
            issuer[0]['last_used_at'] = prior  # Compare in memory only, never restore a DB token.
            require(digest(tokens) == receipt['categories']['tokens'], 'readback changed protected token state')
            current = all_rows(scopes, user)
            require(all(current[k] == v for k, v in receipt['categories'].items() if k != 'tokens'),
                    'owner readback changed protected rows')
            # Full preexisting token rows/fields (including every other token) are
            # sealed immediately after this one GET; raw snapshots stay unaltered.
            before['issuerTokensAfter'] = rows_digest(preexisting)
            return {'undo': json.dumps(before)}
        if action in ('expire-invite', 'status-invite', 'remove-invite'):
            require(invites.count() == 1 and invite.status == InviteStatus.PENDING and not user.is_active,
                    'control needs a genuinely pending invite')
            require(default_token_generator.check_token(user, args['token']), 'control token must remain valid')
            if action == 'expire-invite':
                # 0f047cc94a models/organization_invite.py:78-108: no expires_at.
                invites.update(created_at=datetime.now(timezone.utc) - timedelta(days=INVITE_VALIDITY_DAYS, seconds=1))
            elif action == 'status-invite':
                require(args['status'] in (InviteStatus.EXPIRED, InviteStatus.CANCELLED), 'invalid control status')
                invites.update(status=args['status'])
            else:
                invites.delete()  # Actual missing authority; never a token-invalid substitute.
            invite = invites.first()
        baseline = json.loads(args['undo'])
        require(rows_digest(OrgApiKey._base_manager.filter(organization_id__in=scopes)) == baseline['keys'], 'automatic key mint refused')
        require(all_rows([args['foreignOrg']], exclude_workspace_ids=[args['foreignWorkspaceId']]) ==
                baseline['foreign'], 'foreign victim changed')
        categories = all_rows(scopes, user)
        categories['cache'] = cache_state()
        grants = list(WorkspaceMembership._base_manager.filter(user=user).order_by('workspace_id').values(
            'workspace_id', 'is_active', 'deleted', 'level', 'role', 'workspace__organization_id'))
        orgs = list(OrganizationMembership._base_manager.filter(user=user).values('organization_id', 'is_active', 'deleted', 'level'))
        config = user.config or {}
        return {'categories': categories, 'protectedDigest': digest(categories), 'userId': str(user.pk),
                'userActive': user.is_active, 'status': invite.status if invite else None,
                'isExpired': invite.is_expired if invite else False, 'effectiveStatus': invite.effective_status if invite else None,
                'tokenValid': default_token_generator.check_token(user, args['token']),
                'workspaceIds': [str(g['workspace_id']) for g in grants],
                'activeWorkspaceIds': [str(g['workspace_id']) for g in grants if g['is_active'] and not g['deleted']],
                'grantsValid': len(grants) == 2 and all(g['level'] == 3 and g['role'] == 'workspace_member' and not g['deleted']
                    and str(g['workspace__organization_id']) == args['org'] for g in grants),
                'orgPending': len(orgs) == 1 and str(orgs[0]['organization_id']) == args['org'] and orgs[0]['level'] == 3
                    and not orgs[0]['is_active'] and not orgs[0]['deleted'],
                'selectorsAbsent': not config.get('currentWorkspaceId') and not config.get('defaultWorkspaceId')
                    and not (config.get('orgWorkspaceMap') or {}).get(args['org']),
                'inviteGrants': invite.workspace_access if invite else [], 'inviteCount': invites.count(),
                'systemKeyCount': system_keys.count() + unowned_keys.count(),
                'keyCount': OrgApiKey._base_manager.filter(user=user).count(),
                'tokenCount': AuthToken._base_manager.filter(user=user).count()}

print('E2E-LANDING ' + json.dumps(apply(json.load(sys.stdin))))
