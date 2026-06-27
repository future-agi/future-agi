"""Search-attribute registration (deploy prerequisite).

Kept separate from ``search_attributes.py`` because it imports the operator-API
enums, which must not be pulled into the workflow sandbox. Used by the
``register_eval_task_search_attributes`` management command and by the tests
(to provision the in-memory ``WorkflowEnvironment``).
"""

from __future__ import annotations

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.service import RPCError, RPCStatusCode

from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES


async def register_search_attributes(client, namespace: str) -> bool:
    """Register the eval-task keyword Search Attributes on ``namespace``.

    Idempotent: returns True if it registered them, False if they already
    existed. Any other RPC error propagates.
    """
    request = AddSearchAttributesRequest(
        namespace=namespace,
        search_attributes=dict.fromkeys(
            SEARCH_ATTRIBUTE_NAMES, IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
        ),
    )
    try:
        await client.operator_service.add_search_attributes(request)
        return True
    except RPCError as e:
        if e.status == RPCStatusCode.ALREADY_EXISTS:
            return False
        raise
