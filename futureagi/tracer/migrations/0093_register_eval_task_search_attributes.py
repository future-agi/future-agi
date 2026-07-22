"""Register the eval-task Temporal Search Attributes during `manage.py migrate`.

The eval-task workflows upsert these attributes on start, and upserting an
unregistered attribute wedges the Workflow Task forever — registration must
happen before the workflows run. Baking it into `migrate` guarantees that
ordering without a separate deploy step.

Idempotent both ways: forwards adds only attributes missing from the
namespace, backwards removes only those present, so re-running either
direction is a no-op.

Backwards caveat: removing an attribute that running eval-task workflows
still upsert wedges their Workflow Tasks — only migrate backwards once those
workflows are stopped.

Skipping in test/CI:
- If Temporal is unreachable (nothing listening on TEMPORAL_HOST) the step
  warns and passes through — environments without Temporal are unaffected.
  Run `python manage.py register_eval_task_search_attributes` there instead.
- Set FI_SKIP_TEMPORAL_SA_MIGRATION=1 to skip explicitly.
"""

import asyncio
import os

from django.db import migrations

CONNECT_TIMEOUT_SECONDS = 5


def _skip_reason() -> str | None:
    if os.getenv("FI_SKIP_TEMPORAL_SA_MIGRATION", "").lower() in ("1", "true", "yes"):
        return "FI_SKIP_TEMPORAL_SA_MIGRATION=1"
    try:
        from temporalio.client import Client  # noqa: F401
    except ImportError as e:
        return f"temporalio not importable ({e})"
    return None


async def _connect():
    """Return a connected client, or None (with a warning) if unreachable."""
    from temporalio.client import Client

    from tfc.temporal import TEMPORAL_HOST, TEMPORAL_NAMESPACE

    try:
        return await asyncio.wait_for(
            Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except Exception as e:
        print(
            f"WARNING: Temporal unreachable at {TEMPORAL_HOST} ({e}). "
            "Skipping search-attribute registration."
        )
        print(
            "         Run `python manage.py register_eval_task_search_attributes` "
            "once Temporal is up."
        )
        return None


def forwards(apps, schema_editor):
    reason = _skip_reason()
    if reason:
        print(f"{reason} — skipping Temporal search-attribute registration.")
        return
    asyncio.run(_register())


def backwards(apps, schema_editor):
    reason = _skip_reason()
    if reason:
        print(f"{reason} — skipping Temporal search-attribute removal.")
        return
    asyncio.run(_deregister())


async def _register():
    from tfc.temporal import TEMPORAL_NAMESPACE
    from tfc.temporal.eval_tasks.registration import register_search_attributes
    from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES

    client = await _connect()
    if client is None:
        return

    registered = await register_search_attributes(client, TEMPORAL_NAMESPACE)
    names = ", ".join(SEARCH_ATTRIBUTE_NAMES)
    if registered:
        print(f"Registered Temporal search attributes: {names}")
    else:
        print(f"Temporal search attributes already registered: {names}")


async def _deregister():
    from tfc.temporal import TEMPORAL_NAMESPACE
    from tfc.temporal.eval_tasks.registration import remove_search_attributes
    from tfc.temporal.eval_tasks.search_attributes import SEARCH_ATTRIBUTE_NAMES

    client = await _connect()
    if client is None:
        return

    removed = await remove_search_attributes(client, TEMPORAL_NAMESPACE)
    names = ", ".join(SEARCH_ATTRIBUTE_NAMES)
    if removed:
        print(f"Removed Temporal search attributes: {names}")
    else:
        print(f"Temporal search attributes not registered, nothing to remove: {names}")


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0092_relax_external_eval_config_model_choices"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
