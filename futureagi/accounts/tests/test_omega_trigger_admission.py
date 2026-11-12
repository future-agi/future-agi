import hashlib
import hmac
import json
import time
from unittest.mock import patch
from uuid import uuid4

from rest_framework.test import APIRequestFactory

from accounts.host_events.config import READY, EventChannelConfig
from accounts.tests.test_omega_session_exchange import ExchangeFixture
from accounts.views.omega_trigger_admission import DOMAIN, OmegaTriggerAdmissionView
from omega_rollout.models import ProductAccess, ProductRollout
from tfc.routers import _is_force_primary


class TriggerAdmissionTests(ExchangeFixture):
    def setUp(self):
        super().setUp()
        self.membership.level = 8
        self.membership.role = "Admin"
        self.membership.save(update_fields=["level", "role"])
        self.channel = EventChannelConfig(
            state=READY,
            issuer=self.environ["OMEGA_SQUAD_DELEGATION_ISSUER"],
            key_id="test-channel",
            secret=b"synthetic-host-channel-secret-0001",
        )
        mock = patch(
            "accounts.views.omega_trigger_admission.load_config",
            return_value=self.channel,
        )
        mock.start()
        self.addCleanup(mock.stop)

    def admit(self, *, signed=True, **changes):
        value = {
            "issuer": self.channel.issuer,
            "subject": str(self.user.pk),
            "organizationId": str(self.organization.pk),
            "workspaceId": str(self.workspace.pk),
            "product": "console",
            "nonce": str(uuid4()),
            "timestamp": int(time.time()),
            **changes,
        }
        body = json.dumps(value).encode()
        signature = hmac.new(
            self.channel.secret, DOMAIN + body, hashlib.sha256
        ).hexdigest()
        request = APIRequestFactory().post(
            "/accounts/omega/trigger-admission/",
            body,
            content_type="application/json",
            HTTP_X_OMEGA_ADMISSION_KEY_ID=self.channel.key_id,
            HTTP_X_OMEGA_ADMISSION_SIGNATURE=signature if signed else "0" * 64,
            HTTP_AUTHORIZATION="Bearer not-authority",
        )
        return OmegaTriggerAdmissionView.as_view()(request)

    def test_product_wall_withdrawal_is_live_not_role_only(self):
        self.assertEqual(self.admit().data["products"]["console"], "active")
        ProductRollout.objects.filter(product="console").update(paused=True)
        self.assertEqual(self.admit().data["products"]["console"], "paused")
        ProductRollout.objects.filter(product="console").update(paused=False)
        ProductAccess.objects.filter(
            product="console", organization=self.organization
        ).delete()
        self.assertNotIn("console", self.admit().data["products"])
        self.assertEqual(self.membership.level, 8)

    def test_current_membership_and_role_are_required(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.admit().data["reason"], "owner_removed")
        self.membership.is_active = True
        self.membership.level = 3
        self.membership.save(update_fields=["is_active", "level"])
        self.assertEqual(self.admit().data["reason"], "owner_role_denied")
        self.membership.level = 8
        self.membership.save(update_fields=["level"])
        self.assertEqual(self.admit().data["products"]["console"], "active")

    def test_invalid_signature_and_stale_request_never_read_authority(self):
        with patch.object(OmegaTriggerAdmissionView, "owner_refusal") as lookup:
            empty = APIRequestFactory().post(
                "/accounts/omega/trigger-admission/",
                b"",
                content_type="application/json",
            )
            self.assertEqual(
                OmegaTriggerAdmissionView.as_view()(empty).status_code, 401
            )
            self.assertEqual(self.admit(signed=False).status_code, 401)
            self.assertEqual(
                self.admit(timestamp=int(time.time()) - 60).status_code, 401
            )
            self.assertEqual(self.admit(issuer="other").status_code, 401)
            self.assertEqual(self.admit(product="sandboxes").status_code, 401)
            lookup.assert_not_called()

    def test_live_checks_are_pinned_to_primary_and_no_credentials_are_returned(self):
        original = OmegaTriggerAdmissionView.owner_refusal

        def checked(value):
            self.assertTrue(_is_force_primary())
            return original(value)

        with patch.object(
            OmegaTriggerAdmissionView, "owner_refusal", side_effect=checked
        ):
            response = self.admit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"nonce", "products"})
        self.assertEqual(response["Cache-Control"], "no-store")
