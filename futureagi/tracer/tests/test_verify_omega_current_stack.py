from unittest.mock import Mock

import pytest
from django.core.management.base import CommandError

from tracer.management.commands import verify_omega_current_stack as verification


def test_requires_explicit_ack_before_any_operation(monkeypatch):
    monkeypatch.delenv("OMEGA_E2E_RUN_ACK", raising=False)
    operation = Mock()
    monkeypatch.setattr(verification, "activate", operation)
    with pytest.raises(CommandError, match="acknowledgement"):
        verification.Command().handle(operation="activate")
    operation.assert_not_called()


def test_dispatches_only_fixed_operation(monkeypatch):
    monkeypatch.setenv("OMEGA_E2E_RUN_ACK", "CREATE_ONE_SYNTHETIC_OMEGA_PROJECT")
    operation = Mock()
    monkeypatch.setattr(verification, "preflight", operation)
    verification.Command().handle(operation="preflight")
    operation.assert_called_once_with()
    with pytest.raises(KeyError):
        verification.Command().handle(operation="arbitrary_code")
