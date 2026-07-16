"""Search-attribute registration (deploy prerequisite).

Kept separate from ``search_attributes.py`` because it imports the operator-API
enums, which must not be pulled into the workflow sandbox. Used by the
``register_eval_task_search_attributes`` management command and by the tests
(to provision the in-memory ``WorkflowEnvironment``).
"""

from __future__ import annotations

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
    RemoveSearchAttributesRequest,
)
from temporalio.service import RPCError, RPCStatusCode

from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES


async def register_search_attributes(client, namespace: str) -> bool:
    """Register the eval-task keyword Search Attributes on ``namespace``.

    Idempotent: only attributes missing from the namespace are added —
    AddSearchAttributes rejects the whole batch with ALREADY_EXISTS if any
    single name exists, so a partial prior registration would otherwise never
    complete. Returns True if it added any, False if all already existed.
    """
    existing = await client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=namespace)
    )
    missing = [
        name
        for name in SEARCH_ATTRIBUTE_NAMES
        if name not in existing.custom_attributes
    ]
    if not missing:
        return False

    request = AddSearchAttributesRequest(
        namespace=namespace,
        search_attributes=dict.fromkeys(
            missing, IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
        ),
    )
    try:
        await client.operator_service.add_search_attributes(request)
    except RPCError as e:
        # Lost a race with a concurrent registration — the attributes exist.
        if e.status != RPCStatusCode.ALREADY_EXISTS:
            raise
    return True


async def remove_search_attributes(client, namespace: str) -> bool:
    """Remove the eval-task Search Attributes from ``namespace``.

    Idempotent: only attributes present on the namespace are removed. Returns
    True if it removed any, False if none were registered.

    Removing an attribute that running eval-task workflows still upsert wedges
    their Workflow Tasks — only remove once those workflows are stopped.
    """
    existing = await client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=namespace)
    )
    present = [
        name for name in SEARCH_ATTRIBUTE_NAMES if name in existing.custom_attributes
    ]
    if not present:
        return False

    try:
        await client.operator_service.remove_search_attributes(
            RemoveSearchAttributesRequest(
                namespace=namespace, search_attributes=present
            )
        )
    except RPCError as e:
        # Lost a race with a concurrent removal — the attributes are gone.
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    return True
