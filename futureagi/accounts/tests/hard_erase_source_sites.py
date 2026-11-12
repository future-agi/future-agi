"""Exact source classifications, NOT runtime SQL permissions.

A changed statement or a new call site must be independently reviewed.
"""

SOURCE_SITES = {
    (
        "futureagi/accounts/migrations/0026_authtoken_scope.py",
        "forwards",
        "a1613c5db78118c2cd676cbb7ca5771f327c641cbef8bef698d974ce39bd230f",
    ): (
        "MIGRATION_TEMPLATE",
        "migration boundary: exact historical index template; unresolved source braces are not driver SQL. Expanded forward/reverse executor dispatch must pass the existing migration-route qualification.",
    ),
    (
        "futureagi/ee/usage/utils/usage_sqls.py",
        "get_workspace_usage_sql",
        "9e5d0447ae2989e7132ed6ba47e96f6609a57d6623f8d08affcfc50a18aaa980",
    ): (
        "OUT_OF_SCOPE",
        "Pure-read workspace usage template with a date-filter source interpolation; expanded runtime SQL remains guarded, with no runtime exception.",
    ),
    (
        "futureagi/model_hub/management/commands/backfill_eval_usage_version.py",
        "<module>",
        "bb264a771559f8db1f5e47a28be8b26a0c8dc721b7d6e812206bb26e458b5269",
    ): (
        "OUT_OF_SCOPE",
        "Interpolated backfill updates only usage_apicalllog (reviewed token-count homonym exclusion); no protected table or runtime permission.",
    ),
    (
        "futureagi/model_hub/views/ai_filter.py",
        "_fetch_simulation_field_values",
        "0379ac5e3b89882c7faa68afb2ebeb44e4960637545c2f21543b016fc1ef7788",
    ): (
        "OUT_OF_SCOPE",
        "ClickHouse FINAL read template with value/filter interpolation, not an application PostgreSQL mutation or driver exemption.",
    ),
    (
        "futureagi/tracer/migrations/0085_eval_entry_live_unique_indexes.py",
        "_dedup_sql",
        "9233871d4d2cfabfa3dba275efb8f6115fca6ca41a1794d128041f922016c566",
    ): (
        "OUT_OF_SCOPE",
        "Historical deduplication template updates only tracer_eval_logger (reviewed trace-session homonym exclusion); no protected table or runtime exception.",
    ),
    (
        "futureagi/tracer/services/clickhouse/oss_outbox_cdc.py",
        "requeue_deadletter",
        "10790bf5ad0dff1e4149da5f7702811cd3d348c4efe6c8891c49c0afdc894088",
    ): (
        "OUT_OF_SCOPE",
        "Dynamic condition in the CDC deadletter/outbox requeue template; literal table constants are unprotected CDC queues, not credential stores. Runtime SQL stays guarded.",
    ),
    (
        "futureagi/tracer/tests/test_grouping_recovery_migration.py",
        "assert_recovery_defaults",
        "74ca694f91888ed39f22dfce3f76081d7953fe1ab82cf8639a12b9a9b67cc4b6",
    ): (
        "OUT_OF_SCOPE",
        "Pure-read recovery assertion with a quoted private-schema interpolation; expanded SQL is an ordinary guarded read, no assertion or fixture change.",
    ),
    (
        "futureagi/tracer/tests/test_grouping_recovery_migration.py",
        "test_recovery_migration_preserves_old_inserts_and_rolls_back",
        "0078240b04dc6f21f3352296c952e627ae141d138e5e2b6d7a5a9f5944cf5487",
    ): (
        "OUT_OF_SCOPE",
        "Pure-read historical recovery assertion with a quoted private-schema interpolation; no mutation permission or assertion change.",
    ),
    (
        "futureagi/tracer/utils/trace_ingestion.py",
        "_bulk_create_with_copy",
        "4effbef551c517f3ceea8472a1571af468957fc7f8c81319d0ea7cf0e8a536f8",
    ): (
        "GUARDED_COPY",
        "Dynamic COPY has only Trace/ObservationSpan production callers, both unprotected by the reviewed census. Actual ingestion body/installed CursorWrapper passes unprotected models and refuses a protected substituted model. No runtime exception.",
    ),
    (
        "futureagi/accounts/services/hard_erase_sql.py",
        "qualify_driver",
        "1d2180b817afeb488aa9ec01bbeadf96b7b71f8208315bf9fbf6048153f56377",
    ): (
        "OUT_OF_SCOPE",
        "Guard classification sentinel passed only to guard_execute with a no-op callback; never sent to a SQL driver.",
    ),
    (
        "futureagi/ee/usage/tests/test_ledger_invariants.py",
        "test_i2_catches_a_bucket_of_the_wrong_sign",
        "64ee4730d55c58c4904198fb0d6e3f435046d14ed67eb59a1bf3d0fca88c1606",
    ): (
        "FIXTURE",
        "Intentional corrupt ledger row manufactured through an ephemeral migration; invariant assertions unchanged.",
    ),
    (
        "futureagi/ee/voice/services/vapi_service.py",
        "_end_call",
        "83d04be997adeffb049108146bf79b7bb78b60f53143e5292d7daf3ab78d7ab7",
    ): (
        "OUT_OF_SCOPE",
        "Logging prose, not a SQL dispatch; the phrase Call ended is not CALL syntax.",
    ),
    (
        "futureagi/model_hub/tests/test_ch25_annotation_queue_session_validation_sliceE.py",
        "manufactured",
        "ec6be0ce0bf8ad268fb66ed2951c266a7820f88a8caa5fabc329ffdeef347503",
    ): (
        "FIXTURE",
        "Synthetic AgentDefinition setup and teardown through an ephemeral migration; absent/present and net-new assertions unchanged.",
    ),
    (
        "futureagi/model_hub/tests/test_ch25_annotation_queue_session_validation_sliceE.py",
        "manufactured",
        "0cc163d577bc9a78d23404c65dc5858058313b13f2dfb738b1f841c2ee7f9ae3",
    ): (
        "FIXTURE",
        "Synthetic AgentDefinition setup and teardown through an ephemeral migration; absent/present and net-new assertions unchanged.",
    ),
    (
        "futureagi/scripts/setup_peerdb_mirrors.sql",
        "<module>",
        "a108bb7713a3aee684edd75ce681d8735de1098d8ac729e7eb085f4f762fc129",
    ): (
        "OUT_OF_SCOPE",
        "Deprecated PeerDB control-plane reference, not SQL dispatched to the application PostgreSQL connection.",
    ),
    (
        "futureagi/simulate/services/branch_deviation_analyzer.py",
        "_create_analysis_prompt",
        "81028917b9f4a9a4d5acd60cd3c2f847b3625fd22544e6b79ffbbe55ef8ea0a8",
    ): ("OUT_OF_SCOPE", "Prompt section title, not a SQL statement or driver input."),
    (
        "futureagi/tfc/telemetry/temporal.py",
        "get_temporal_tracing_interceptor",
        "7f74e3000e84fc0aed59d7cfaf1d64722b85a0da45c500f2cb3eb22b37fa3352",
    ): ("OUT_OF_SCOPE", "Structured logging hint, not a database command."),
    (
        "futureagi/tracer/services/clickhouse/oss_cdc_migrations/cdc_001_catalog_target_fields.sql",
        "<module>",
        "70fbc6d415c486ba9ff12640c188cfe0f38a49cfb0b8270d293b4bd1181ab42b",
    ): (
        "OUT_OF_SCOPE",
        "ClickHouse destination schema, not the application PostgreSQL table of the same name.",
    ),
    (
        "futureagi/tracer/services/clickhouse/schema.py",
        "<module>",
        "81dd8e23797c027789de82add0b48a5b01247bdba06e17e734a3ac4d502ec1e1",
    ): (
        "OUT_OF_SCOPE",
        "ClickHouse ReplicatedReplacingMergeTree destination schema, not application PostgreSQL.",
    ),
    (
        "futureagi/tracer/services/clickhouse/tests/test_oss_cdc_upgrade.py",
        "<module>",
        "9ce458a4c615e61711baaee22d0fa3c11c8e961c71e67ec5f78c4b8d204b1678",
    ): (
        "OUT_OF_SCOPE",
        "ClickHouse schema expectation with an in-memory client double; no PostgreSQL dispatch.",
    ),
    (
        "futureagi/tracer/services/clickhouse/tests/test_oss_outbox_cdc_pg.py",
        "test_every_write_path_is_captured_and_mirrored",
        "862e2d3f8f2bd8062b3bec96e9767b24b7fd1d4704adea36f0d641fbb600c819",
    ): (
        "OUT_OF_SCOPE",
        "Native-driver isolated scratch database with independently manufactured CDC tables, not a Django application identity database.",
    ),
    (
        "futureagi/tracer/tests/test_observability_switch.py",
        "agent_update_columns",
        "7f08b2613aa3efd7dd04e81986121ab7649439c871b047a7a4d71ff42ce89bc3",
    ): (
        "OUT_OF_SCOPE",
        "Regex inspecting captured ORM SQL; the regex is never executed as SQL.",
    ),
    (
        "futureagi/tracer/tests/test_simulation_project.py",
        "<module>",
        "c7b11174064ec43b3b55a76208156021df680ed0b883bdcf0da56d6790d6b00e",
    ): (
        "FIXTURE",
        "Collector SQL upsert remains the subject of the regression, dispatched through a migration fixture; all RETURNING/conflict assertions unchanged.",
    ),
    (
        "futureagi/webhooks/tests/test_api_wave3b.py",
        "test_f6_the_endpoint_created_index_answers_the_registrys_24h_count",
        "7c661a4dad944c12334ad64c796c9fa549b3053087d31d70ff1159e732dfca6e",
    ): (
        "FIXTURE",
        "Aged delivery timestamps and ANALYZE are migration fixtures; index-plan and aggregate assertions unchanged.",
    ),
    (
        "futureagi/webhooks/tests/test_api_wave3b.py",
        "test_f6_the_endpoint_created_index_answers_the_registrys_24h_count",
        "b8428148be622f0505e032ccb10059b5b0f483eb32591c05dd298f336421a198",
    ): (
        "FIXTURE",
        "Aged delivery timestamps and ANALYZE are migration fixtures; index-plan and aggregate assertions unchanged.",
    ),
    (
        "futureagi/tracer/tests/test_grouping_recovery_migration.py",
        "test_recovery_migration_preserves_old_inserts_and_rolls_back",
        "7454e2724058bad44953f4bd6188d1ad5b70b7c16adf966ced9e8b6081b2c592",
    ): (
        "FIXTURE",
        "Private-schema historical recovery fixture: raw copy/delete and actual forward/reverse migration dispatch through the ephemeral executor; assertions unchanged.",
    ),
    (
        "futureagi/agentcc/tests/test_guardrail_write_paths.py",
        "<module>",
        "2dd7999bea5a641d80fa7e34b4240ec7104b05c0e1ef35afbed6a7a4a889308b",
    ): (
        "OUT_OF_SCOPE",
        "Regex inspects captured ORM row-lock SQL; it is never dispatched as SQL.",
    ),
    (
        "futureagi/tracer/tests/test_grouping_recovery_migration.py",
        "insert_old_columns",
        "66af883f56d9d3f9ebd05e2bf09f3224ab20d4dc70e5d5eebbe3667e51579655",
    ): (
        "FIXTURE",
        "Historical INSERT selector fragment inside the private-schema migration fixture; not independently dispatched.",
    ),
}
