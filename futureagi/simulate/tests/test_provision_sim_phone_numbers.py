"""Tests for the provision_sim_phone_numbers management command (TH-5642)."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from simulate.models.simulation_phone_number import SimulationPhoneNumber


class _Resp:
    status_code = 200

    @staticmethod
    def json():
        return {
            "incoming_phone_numbers": [
                {"phone_number": "+12175696753", "sid": "PN111"},
                {"phone_number": "+12068956991", "sid": "PN222"},
            ]
        }


@pytest.mark.django_db
def test_provisions_idle_outbound_numbers():
    with patch("requests.get", return_value=_Resp()):
        call_command("provision_sim_phone_numbers", "--direction", "outbound",
                     "--sid", "ACx", "--token", "t", stdout=StringIO())
    rows = SimulationPhoneNumber.objects.filter(call_direction="outbound")
    assert rows.count() == 2
    r = rows.get(provider_phone_id="PN111")
    assert r.phone_number == "+12175696753"
    assert r.status == SimulationPhoneNumber.PhoneStatus.IDLE


@pytest.mark.django_db
def test_idempotent_skips_existing():
    SimulationPhoneNumber.objects.create(
        phone_number="+12175696753", provider_phone_id="PN111",
        call_direction="outbound", status=SimulationPhoneNumber.PhoneStatus.IDLE)
    with patch("requests.get", return_value=_Resp()):
        call_command("provision_sim_phone_numbers", "--direction", "outbound",
                     "--sid", "ACx", "--token", "t", stdout=StringIO())
    # PN111 already existed → only PN222 added; no duplicate.
    assert SimulationPhoneNumber.objects.filter(provider_phone_id="PN111").count() == 1
    assert SimulationPhoneNumber.objects.count() == 2


@pytest.mark.django_db
def test_numbers_filter_and_dry_run():
    with patch("requests.get", return_value=_Resp()):
        call_command("provision_sim_phone_numbers", "--direction", "outbound",
                     "--numbers", "+12175696753", "--dry-run",
                     "--sid", "ACx", "--token", "t", stdout=StringIO())
    # dry-run creates nothing; filter would have limited to one anyway.
    assert SimulationPhoneNumber.objects.count() == 0


@pytest.mark.unit
def test_requires_creds():
    with pytest.raises(CommandError):
        call_command("provision_sim_phone_numbers", "--direction", "outbound",
                     "--sid", "", "--token", "", stdout=StringIO())
