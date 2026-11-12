"""The managed-credit API's OpenAPI definition names are its own.

With the EE package installed, ``generate_swagger`` builds the managed-credit
console API (/agentcc/managed-credits/) and the platform billing API into one
schema. drf-yasg names each serializer's definition after its class
(``FooSerializer`` becomes ``Foo``) and refuses the whole schema
(``SwaggerGenerationError``) when two distinct serializers share that name
without an explicit ``Meta.ref_name``; with explicit names, one definition
silently describes both. The billing serializers live in
``ee.usage.serializers``, so a managed-credit serializer must never take one
of their names.
"""

import inspect

import pytest
from drf_yasg.utils import get_serializer_ref_name
from rest_framework import serializers

from agentcc.serializers import managed_credits

# The billing serializers ship in the enterprise ee/ package. A build without
# it (the OSS lane) has no platform billing API to share a name with, so the
# module is skipped there instead of failing to collect.
billing_contracts = pytest.importorskip("ee.usage.serializers.contracts")
billing_usage = pytest.importorskip("ee.usage.serializers.usage")

pytestmark = pytest.mark.requires_ee


def _definition_names(*modules):
    """Definition name -> the serializer classes a module defines under it."""
    names = {}
    for module in modules:
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or not issubclass(
                cls, serializers.BaseSerializer
            ):
                continue
            # drf-yasg reads only the class name and Meta.ref_name.
            ref_name = get_serializer_ref_name(cls.__new__(cls))
            if ref_name:
                names.setdefault(ref_name, []).append(cls.__qualname__)
    return names


def test_managed_credit_definitions_never_take_a_billing_serializers_name():
    ours = _definition_names(managed_credits)
    billing = _definition_names(billing_contracts, billing_usage)
    assert ours, "no managed-credit serializers found"
    shared = sorted(set(ours) & set(billing))
    assert shared == [], {name: (ours[name], billing[name]) for name in shared}
