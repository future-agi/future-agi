"""Operate the Omega product rollout from a shell (READINESS 3.13). Every change names the staff
operator who makes it (`--actor`: active staff holding operate_rollout, with an organization) and a
reason, and is audited like a change made through the API.

    manage.py omega_rollout status
    manage.py omega_rollout stage console S1 --actor ops@futureagi.com --reason "partners signed"
    manage.py omega_rollout percentage hub 25 --actor ... --reason ...
    manage.py omega_rollout allow console --org <uuid> [--workspace <uuid>] [--cohort partner] --actor ... --reason ...
    manage.py omega_rollout deny sandboxes --org <uuid> --workspace <uuid> --actor ... --reason ...
    manage.py omega_rollout remove console --org <uuid> [--workspace <uuid>] --actor ... --reason ...
    manage.py omega_rollout kill deployments --actor ... --reason "P0 #123" [--pause-installs]
    manage.py omega_rollout restore deployments --actor ... --reason ...

`all` in place of a product applies an allow, deny or remove to every hosted product.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from omega_rollout import audit, ops
from omega_rollout import products as p
from omega_rollout.authority import home_organization, is_operator, require_operator

ACTIONS = (
    "status",
    "stage",
    "percentage",
    "allow",
    "deny",
    "remove",
    "kill",
    "restore",
    "grant-operator",
    "revoke-operator",
)


class Command(BaseCommand):
    help = "Read or change the rollout of the hosted Omega products (staff operators only)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=ACTIONS)
        parser.add_argument("product", nargs="?")
        parser.add_argument("value", nargs="?")
        parser.add_argument("--org")
        parser.add_argument("--workspace")
        parser.add_argument(
            "--cohort", default=p.COHORT_PARTNER, choices=list(p.COHORTS)
        )
        parser.add_argument(
            "--actor", help="email of the staff operator making the change"
        )
        parser.add_argument("--email", help="person whose operator grant changes")
        parser.add_argument("--by", help="active superuser approving the operator change")
        parser.add_argument("--reason", default="")
        parser.add_argument("--pause-installs", action="store_true")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "status":
            return self._status()
        if action in ("grant-operator", "revoke-operator"):
            return self._operator_change(action, options)
        actor = self._actor(options["actor"])
        products = self._products(
            options["product"], allow_all=action in ("allow", "deny", "remove")
        )
        try:
            # `all` changes every product or none: a refusal for one rolls back the others.
            with transaction.atomic():
                for product in products:
                    self._apply(action, product, options, actor)
        except ops.RolloutChangeRefused as exc:
            raise CommandError(f"{exc.code}: {exc}") from exc
        self._status()

    def _operator_change(self, action, options):
        from django.contrib.auth.models import Permission

        from accounts.models import User

        if options["product"] is not None or options["value"] is not None:
            raise CommandError("No product or value is accepted for operator changes.")
        try:
            reason = ops._require_reason(options["reason"])
        except ops.RolloutChangeRefused as exc:
            raise CommandError(str(exc)) from exc

        with transaction.atomic():
            person = self._operator_identity(
                User.objects.select_for_update(), options["email"]
            )
            approver = self._operator_identity(User.objects, options["by"])
            if person is None or not person.is_active:
                raise CommandError("--email must name an existing active person.")
            if approver is None or not approver.is_active or not approver.is_superuser:
                raise CommandError("--by must name an existing active superuser.")
            if person.pk == approver.pk:
                raise CommandError("--by and --email must name different people.")
            organization = home_organization(person)
            if organization is None:
                raise CommandError(
                    "The person has no active organization, so the change could not be audited."
                )
            permission = Permission.objects.get(
                content_type__app_label="omega_rollout", codename="operate_rollout"
            )
            before = self._operator_snapshot(person, permission)
            granting = action == "grant-operator"
            if granting:
                if not person.is_staff:
                    person.is_staff = True
                    person.save(update_fields=["is_staff"])
                if not before["operate_rollout"]:
                    person.user_permissions.add(permission)
            elif before["operate_rollout"]:
                person.user_permissions.remove(permission)
            # Permission checks cache on each instance; reload before reporting effective access.
            person = User.objects.get(pk=person.pk)
            after = self._operator_snapshot(person, permission)
            audit.record(
                action="operator.granted" if granting else "operator.revoked",
                product="",
                actor=approver,
                organization=organization,
                actor_organization=organization,
                before=before,
                after=after,
                reason=reason,
                epoch=0,
            )

        notice = ""
        if granting:
            if before == after:
                notice = " (unchanged)"
        elif not before["operate_rollout"]:
            notice = " (nothing to revoke)"
        elif after["operator"]:
            notice = (
                " (direct grant removed; still an operator through a group or superuser status; "
                "staff status kept)"
            )
        else:
            notice = " (staff status kept)"
        self.stdout.write(
            f"{action} {person.email}: operator now = {'yes' if after['operator'] else 'no'}{notice}"
        )

    @staticmethod
    def _operator_identity(users, email):
        matches = list(users.filter(email__iexact=email)[:2])
        if len(matches) > 1:
            raise CommandError(
                "More than one account matches that address when letter case is ignored; "
                "nothing was changed."
            )
        return matches[0] if matches else None

    @staticmethod
    def _operator_snapshot(person, permission):
        return {
            "user_id": str(person.pk),
            "email": person.email,
            "is_staff": person.is_staff,
            "operate_rollout": person.user_permissions.filter(pk=permission.pk).exists(),
            "operator": is_operator(person),
        }

    def _apply(self, action, product, options, actor):
        reason = options["reason"]
        if action == "stage":
            ops.set_stage(product, options["value"] or "", actor=actor, reason=reason)
        elif action == "percentage":
            try:
                value = int(options["value"] or "")
            except ValueError as exc:
                raise CommandError("percentage needs a whole number 0-100") from exc
            ops.set_percentage(product, value, actor=actor, reason=reason)
        elif action == "kill":
            ops.kill(
                product,
                actor=actor,
                reason=reason,
                pause_installs=options["pause_installs"],
            )
        elif action == "restore":
            ops.restore(product, actor=actor, reason=reason)
        else:
            organization, workspace = self._scope(options["org"], options["workspace"])
            if action == "remove":
                ops.remove_access(
                    product,
                    organization,
                    workspace=workspace,
                    actor=actor,
                    reason=reason,
                )
            else:
                ops.set_access(
                    product,
                    organization,
                    effect=action,
                    cohort=options["cohort"],
                    workspace=workspace,
                    actor=actor,
                    reason=reason,
                )

    def _status(self):
        from omega_rollout.models import ProductAccess, ProductRollout, RolloutEpoch

        rows = {row.product: row for row in ProductRollout.objects.all()}
        out = {
            "epoch": RolloutEpoch.objects.filter(pk=1)
            .values_list("epoch", flat=True)
            .first()
            or 0,
            "products": {},
        }
        for product in p.HOSTED_PRODUCTS:
            row = rows.get(product) or ProductRollout(product=product)
            out["products"][product] = {
                "stage": row.stage,
                "percentage": row.percentage,
                "paused": row.paused,
                "installs_paused": row.installs_paused,
                "allow": ProductAccess.objects.filter(
                    product=product, effect=p.EFFECT_ALLOW
                ).count(),
                "deny": ProductAccess.objects.filter(
                    product=product, effect=p.EFFECT_DENY
                ).count(),
            }
        self.stdout.write(json.dumps(out, indent=2))

    def _actor(self, email):
        from accounts.models import User

        if not email:
            raise CommandError(
                "--actor <staff email> is required: every change names its operator"
            )
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            raise CommandError("--actor must be an active staff operator")
        try:
            # The API's rule: staff, operate_rollout, and an org to audit the change under.
            return require_operator(user)
        except ops.RolloutChangeRefused as exc:
            raise CommandError(f"{exc.code}: {exc}") from exc

    def _products(self, product, *, allow_all):
        if product == "all" and allow_all:
            return list(p.HOSTED_PRODUCTS)
        if not p.is_product(product):
            raise CommandError(f"product must be one of {', '.join(p.HOSTED_PRODUCTS)}")
        return [product]

    def _scope(self, org_id, workspace_id):
        from accounts.models import Organization
        from accounts.models.workspace import Workspace

        if not org_id:
            raise CommandError("--org <uuid> is required")
        organization = Organization.objects.filter(pk=org_id).first()
        if organization is None:
            raise CommandError("no such organization")
        workspace = None
        if workspace_id:
            workspace = Workspace.no_workspace_objects.filter(
                pk=workspace_id, organization=organization
            ).first()
            if workspace is None:
                raise CommandError("no such workspace in that organization")
        return organization, workspace
