"""Preserve c2.5 database effects while applying the renamed c2.6 state.

These are aliases for identical historical operations, not squash replacements.
Never mutate migration history here: the executor records the new name after a
successful apply. Keeping the old row also makes reversing the alias a database
no-op on a legacy installation. This is not a schema-drift repair mechanism.
"""

from django.db.migrations.operations.base import Operation, OperationCategory
from django.db.migrations.recorder import MigrationRecorder


# App labels, not package names: agentcc's migration label is prism.
# Empty merge migrations are included for history/plan validators as well.
LEGACY_APPLIED_NAMES = {
    ("prism", "0085_dispatch_claim"): ("prism", "0075_dispatch_claim"),
    ("prism", "0086_merge_dispatch_claim_webhook_at_rest"): (
        "prism", "0078_merge_dispatch_claim_webhook_at_rest"
    ),
    ("prism", "0083_managed_credit_checkout_binding"): (
        "prism", "0079_managed_credit_checkout_binding"
    ),
    ("prism", "0084_checkout_actor_identifier"): (
        "prism", "0080_checkout_actor_identifier"
    ),
    ("simulate", "0102_agent_definition_encrypted_api_key"): (
        "simulate", "0100_agent_definition_encrypted_api_key"
    ),
    ("simulate", "0102_prompt_optimizer_tenant_lineage"): (
        "simulate", "0100_prompt_optimizer_tenant_lineage"
    ),
    ("simulate", "0103_encrypt_agent_definition_api_key"): (
        "simulate", "0101_encrypt_agent_definition_api_key"
    ),
    ("simulate", "0104_remove_agent_definition_api_key"): (
        "simulate", "0102_remove_agent_definition_api_key"
    ),
    ("simulate", "0105_merge_api_key_at_rest_tenant_lineage"): (
        "simulate", "0103_merge_api_key_at_rest_tenant_lineage"
    ),
    ("simulate", "0103_merge_sandbox_harness_job"): (
        "simulate", "0104_merge_sandbox_harness_job"
    ),
    ("accounts", "0038_organizationmembership_removed_at"): (
        "accounts", "0037_organizationmembership_removed_at"
    ),
    # c2.5.x add-on (batch 4), not in the c2.5 FINAL history: a first-appearance
    # twin bridged the same way so c2.5.x databases skip its database step.
    ("prism", "0089_normalize_two_key_org_period"): (
        "prism", "0081_normalize_two_key_org_period"
    ),
}


class SkipDatabaseIfLegacyApplied(Operation):
    """Always advance state; run database operations only without the old row.

    The intermediate-state loops follow SeparateDatabaseAndState. PostgreSQL's
    migration-level transaction remains authoritative. The sole non-atomic
    bridged migration contains one explicitly non-atomic RunPython operation;
    retain that flag rather than wrapping its bounded batches in a transaction.
    """

    category = OperationCategory.MIXED
    serialization_expand_args = ["operations"]
    elidable = False

    def __init__(self, legacy, operations):
        self.legacy = tuple(legacy)
        self.operations = list(operations)

    @property
    def reversible(self):
        return all(operation.reversible for operation in self.operations)

    @property
    def reduces_to_sql(self):
        # Never execute a RunPython while collecting SQL. A mixed wrapper is
        # represented by Django's non-SQL comment, not incomplete runnable SQL.
        return all(operation.reduces_to_sql for operation in self.operations)

    @property
    def atomic(self):
        if len(self.operations) == 1:
            return self.operations[0].atomic
        return False

    def deconstruct(self):
        return (
            self.__class__.__qualname__,
            [],
            {"legacy": self.legacy, "operations": self.operations},
        )

    def describe(self):
        return "Apply database changes unless %s.%s is recorded" % self.legacy

    def reduce(self, operation, app_label):
        # The recorded-name guard must never disappear or move across a peer.
        return False

    def state_forwards(self, app_label, state):
        for operation in self.operations:
            operation.state_forwards(app_label, state)

    def _legacy_applied(self, schema_editor):
        return self.legacy in MigrationRecorder(
            schema_editor.connection
        ).applied_migrations()

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self._legacy_applied(schema_editor):
            return
        for operation in self.operations:
            to_state = from_state.clone()
            operation.state_forwards(app_label, to_state)
            operation.database_forwards(
                app_label, schema_editor, from_state, to_state
            )
            from_state = to_state

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if self._legacy_applied(schema_editor):
            return
        to_states = []
        for operation in self.operations:
            to_states.append((operation, to_state))
            to_state = to_state.clone()
            operation.state_forwards(app_label, to_state)
        for operation, previous_state in reversed(to_states):
            from_state = to_state
            to_state = previous_state
            operation.database_backwards(
                app_label, schema_editor, from_state, to_state
            )
