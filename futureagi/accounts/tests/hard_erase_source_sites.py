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
        "futureagi/mcp_server/migrations/0005_personal_key_reconnect.py",
        "<module>",
        "c1ebba9d898571934da749f9531e3cfa6c52f969f33beb9f44b978cba72cee2e",
    ): (
        "MIGRATION_TEMPLATE",
        "migration boundary: exact column add of the reconnect flag, catalog-only DDL with a constant default that reads and writes no row or credential column. Forward/reverse executor dispatch must pass the migration-route qualification; outside it the guard refuses.",
    ),
    (
        "futureagi/mcp_server/migrations/0005_personal_key_reconnect.py",
        "<module>",
        "617d8f32c17df274238c016f78d9c561a93019acbc8596a712a7e48b585d2f44",
    ): (
        "MIGRATION_TEMPLATE",
        "migration boundary: exact reverse drop of the reconnect flag column only, no credential column. Forward/reverse executor dispatch must pass the migration-route qualification; outside it the guard refuses.",
    ),
    (
        "futureagi/mcp_server/migrations/0007_reconnect_required_db_default.py",
        "<module>",
        "93169be4af52c2250409f14a0c98ed41a8effe3f8e3c1524e7f7a91ab2072799",
    ): (
        "MIGRATION_TEMPLATE",
        "migration boundary: exact SET DEFAULT false on the reconnect flag, catalog-only DDL that reads and writes no row or credential column. Forward executor dispatch must pass the migration-route qualification (the reverse is a no-op); outside it the guard refuses.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_default_migration.py",
        "<module>",
        "ce9ec47b4e38fac24959a8f0400d5f8a73ce9431174f321bf232b91e1c34f0e5",
    ): (
        "TEST_DATABASE_ONLY",
        "Test setup on the pytest test database: a second native psycopg connection removes the reconnect flag's database default to recreate the column an earlier 0005 left; catalog only, no row or credential column, and the test puts the default back. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_default_migration.py",
        "<module>",
        "93169be4af52c2250409f14a0c98ed41a8effe3f8e3c1524e7f7a91ab2072799",
    ): (
        "TEST_DATABASE_ONLY",
        "Test cleanup on the pytest test database: a second native psycopg connection puts back the reconnect flag's database default of false; catalog only, no row or credential column. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_default_migration.py",
        "<module>",
        "a8818bb94c447e92e3eae9ef2901d01bbc2626102870a87ff39677fafbeaf0c0",
    ): (
        "TEST_DATABASE_ONLY",
        "Expected sqlmigrate output line that the test compares with the printed text; never executed.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_default_migration.py",
        "insert_without_the_flag",
        "1151cf136dfd5315ed2aca73f397e3feacba9ea7a3a5f7cce65e321d0e44a523",
    ): (
        "TEST_DATABASE_ONLY",
        "Test probe on the pytest test database: a second native psycopg connection copies one test-seeded connection under a new id, naming every column but reconnect_required, as a previous-release insert; the copied credential columns are the seeded row's, which the test leaves unset. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_migration_locks.py",
        "<module>",
        "7860c05361c1d4dd531a1bfe9e27ad43bbd9218bb89cc33e58c2ca0a874d1e40",
    ): (
        "TEST_DATABASE_ONLY",
        "Test probe on the pytest test database: a second native psycopg connection tries FOR UPDATE NOWAIT on one test-seeded connection to see whether the migration holds that row; it reads only the id and changes nothing. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_migration_locks.py",
        "insert_as_previous_release",
        "1151cf136dfd5315ed2aca73f397e3feacba9ea7a3a5f7cce65e321d0e44a523",
    ): (
        "TEST_DATABASE_ONLY",
        "Test probe on the pytest test database: a second native psycopg connection copies one test-seeded connection under a new id, naming every column but reconnect_required, as a previous-release insert; the copied columns include the seeded row's test organization key id, and its OAuth columns are unset. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_migration_locks.py",
        "test_the_column_add_is_a_no_op_when_the_column_is_already_there",
        "fb7f194fd3865588a5d03bb0cb52ef337933ff7de3111bec23492f9bfbdc4d46",
    ): (
        "TEST_DATABASE_ONLY",
        "Test setup on the pytest test database: a second native psycopg connection adds the reconnect flag column as a stopped 0005 run left it; catalog only, no row or credential column. Not application SQL; it never passes through the Django cursor.",
    ),
    (
        "futureagi/mcp_server/tests/test_reconnect_migration_locks.py",
        "test_the_column_add_is_a_no_op_when_the_column_is_already_there",
        "46d4c9d499ec0788b0e40c8f17f063f40536eee53f7c0879467c1035845a4040",
    ): (
        "TEST_DATABASE_ONLY",
        "Test cleanup on the pytest test database: a second native psycopg connection drops the reconnect flag column when the migration did not apply; no credential column. Not application SQL; it never passes through the Django cursor.",
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
    (
        "deploy/helm/futureagi/hack/test_generated_keys.py",
        "<module>",
        "717cf86b5d22733f5ae47b5267eafb513810b3c673ac837e044dadbb3a29ec43",
    ): (
        "OUT_OF_SCOPE",
        "English Helm refusal message, not SQL: secrets.existingSecret is a chart values key, not the secrets table. The string is only compared with helm template stderr; the file runs helm through subprocess and has no database driver.",
    ),
    (
        "deploy/tests/test_observed_catalog_startup.py",
        "<module>",
        "6541af5fd1caf56055beb6b54650b79cdfa04c844d7e82fdc480c9ffaf639f7e",
    ): (
        "OUT_OF_SCOPE",
        "Shell loop text the installer loop rule must refuse; the test matches it against that rule's regular expression, and the file has no database driver.",
    ),
    (
        "deploy/tests/test_observed_catalog_startup.py",
        "<module>",
        "d74df8873794347f7b3dae23ee1be5336a1c078ea0da98781bc5ede0bc3fa94a",
    ): (
        "OUT_OF_SCOPE",
        "Shell loop text the installer loop rule must refuse; the test matches it against that rule's regular expression, and the file has no database driver.",
    ),
    (
        "deploy/tests/test_update_install_cli.py",
        "test_journal_hint_names_the_flags_of_the_install_it_belongs_to",
        "e8a3b39dfde5bbd8e538012ac682cb6c791be723f502be611e8fbdebdae71f1e",
    ): (
        "OUT_OF_SCOPE",
        "Expected command-line prefix of a recovery hint, compared by the test with the journal reason text; the file has no database driver.",
    ),
    (
        "deploy/tests/test_update_postgres_native.py",
        "test_stale_zero_and_positive_statistics_never_drive_admission",
        "b403f546250bce6e0e75dc6623a670ef1e9bf651877632a3dea77b6f7e1bf911",
    ): (
        "OUT_OF_SCOPE",
        "Native-driver test against a disposable CI PostgreSQL database: the template deletes from the test's own items table in a schema the test creates and drops, never from the application database.",
    ),
    (
        "deploy/tests/update_e2e/distributed.py",
        "<module>",
        "358a3adad7a8e01978154a2b91d8710fe45d514adbd08fd9bc7aefe9978c8f13",
    ): (
        "OUT_OF_SCOPE",
        "Env-file template of the end-to-end fixture, with notes and empty settings, that write_distributed_files writes as a checkout's .env.example; the file has no database driver.",
    ),
    (
        "deploy/tests/update_e2e/harness.py",
        "<module>",
        "301be2ffe7c3097bc52cc4a36b82e0c13115f247f9d9f0397b1cd2cd30899348",
    ): (
        "OUT_OF_SCOPE",
        "Env-file template of the end-to-end fixture, with notes, empty settings and placeholders, that the harness renders as a checkout's .env.example; the file has no database driver.",
    ),
    (
        "futureagi/accounts/tests/test_hard_erase_backfill_boundary_flow.py",
        "callback",
        "7308bd7f8430e5a6e64bf3933f0c2345e4f41d7e46f0a75608363853b4fd0f21",
    ): (
        "OUT_OF_SCOPE",
        "Offline negative control: the harness cursor sends it through the SQL guard to a recording stand-in driver and asserts the refusal; the test opens no database connection.",
    ),
    (
        "futureagi/accounts/tests/test_hard_erase_backfill_boundary_flow.py",
        "callback",
        "31e816e9da5f5729603177b411f21d65356ea1886be7709fe23d1442e15612ed",
    ): (
        "OUT_OF_SCOPE",
        "Offline negative control: the harness cursor sends it through the SQL guard to a recording stand-in driver and asserts the refusal; the test opens no database connection.",
    ),
    (
        "futureagi/accounts/tests/test_hard_erase_backfill_boundary_flow.py",
        "test_callback_cannot_borrow_locking_read_or_dml_authority",
        "8deff4bf82035211be86d663d5847ad3a9b1a71db7d38f615adc8ff9d70f2592",
    ): (
        "OUT_OF_SCOPE",
        "Offline negative control: the harness cursor sends this locking read through the SQL guard to a recording stand-in driver and asserts the refusal; the test opens no database connection.",
    ),
    (
        "futureagi/accounts/tests/test_hard_erase_backfill_boundary_flow.py",
        "test_driver_failure_cleans_up_without_granting_authority",
        "7308bd7f8430e5a6e64bf3933f0c2345e4f41d7e46f0a75608363853b4fd0f21",
    ): (
        "OUT_OF_SCOPE",
        "Offline negative control: the harness cursor sends it through the SQL guard to a recording stand-in driver and asserts the refusal; the test opens no database connection.",
    ),
    (
        "futureagi/agentcc/tests/test_microsoft_connect_gate.py",
        "session_metadata_write",
        "8d5b3fd927d0054f6ddb615198897faacfa16950c560f2add1217fd409223d4b",
    ): (
        "OUT_OF_SCOPE",
        "Regular expression that re.fullmatch applies to captured ORM SQL in the no-effects observer; it is never executed as SQL.",
    ),
    (
        "futureagi/agentcc/tests/test_microsoft_connect_gate.py",
        "session_metadata_write",
        "149bf343dd76e474f3d13d1c56b1c79fd5999a99547a7d5837258b250f87f831",
    ): (
        "OUT_OF_SCOPE",
        "Regular expression that re.fullmatch applies to captured ORM SQL in the no-effects observer; it is never executed as SQL.",
    ),
    (
        "futureagi/tests/test_update_safety_gate_django.py",
        "test_runsql_all_statement_forms_and_comments_require_review",
        "cbdfd7caf8ba84c801b25489185f428c81a4924126e0fc7d9b66e7b53ab378dd",
    ): (
        "OUT_OF_SCOPE",
        "Fixture SQL for the static migration safety check: the test writes it as a temporary migration file that the check reads through a MigrationLoader with no connection; no database connection opens.",
    ),
    (
        "futureagi/tests/test_update_safety_gate_django.py",
        "test_runsql_all_statement_forms_and_comments_require_review",
        "b32f3e458225b77973f4a509f9aec64e093bf07c549a80b381daff0bf27ea734",
    ): (
        "OUT_OF_SCOPE",
        "Fixture SQL for the static migration safety check: the test writes it as a temporary migration file that the check reads through a MigrationLoader with no connection; no database connection opens.",
    ),
    (
        "futureagi/tests/test_update_safety_gate_django.py",
        "test_runsql_all_statement_forms_and_comments_require_review",
        "c70130e0f3409a73b53c27f0b74f4a861fca8ce44c52c737e2ebbfa46e71fc17",
    ): (
        "OUT_OF_SCOPE",
        "Fixture SQL for the static migration safety check: the test writes it as a temporary migration file that the check reads through a MigrationLoader with no connection; no database connection opens.",
    ),
    (
        "scripts/update_rehearsal_shipping.py",
        "<module>",
        "7bf8ea4de9cd148c73fd4ac73dae65830e67870ea36455ba610406d809057390",
    ): (
        "OUT_OF_SCOPE",
        "Python program text the rehearsal driver runs with python -c in the app container of its disposable install; it checks model metadata and seeds reference rows through the Django ORM, and holds no SQL statement for a driver.",
    ),
    (
        "futureagi/accounts/tests/test_transactional_flush_under_hard_erase_guard.py",
        "test_raw_truncate_outside_flush_is_refused",
        "ad53dcf6a5f1525341642aff4bde700d5033c18cd639e49d164cbc1e9a8e6988",
    ): (
        "OUT_OF_SCOPE",
        "Negative control on the test PostgreSQL database: outside a flush, the installed guard must refuse this raw statement before the driver, and the test asserts that refusal; no row is removed.",
    ),
    (
        "futureagi/accounts/tests/test_transactional_flush_under_hard_erase_guard.py",
        "test_explicit_flush_is_atomic_scoped_and_keeps_cascade",
        "ad53dcf6a5f1525341642aff4bde700d5033c18cd639e49d164cbc1e9a8e6988",
    ): (
        "OUT_OF_SCOPE",
        "Negative control on the test PostgreSQL database: right after a real flush, the installed guard must still refuse this raw statement before the driver, so a flush leaves no authority behind; the test asserts that refusal.",
    ),
    (
        "futureagi/accounts/tests/test_transactional_flush_under_hard_erase_guard.py",
        "handle",
        "ad53dcf6a5f1525341642aff4bde700d5033c18cd639e49d164cbc1e9a8e6988",
    ): (
        "OUT_OF_SCOPE",
        "Negative control on the test PostgreSQL database: inside a management command other than flush, the installed guard must refuse this raw statement before the driver, and the test asserts that refusal.",
    ),
    (
        "futureagi/simulate/tests/test_agent_api_key_migration_offline.py",
        "<module>",
        "b9a3487ee83e25a0695665439ac46fdbc1a0c1bca265f64d87b33ae2fc26d00f",
    ): (
        "OUT_OF_SCOPE",
        "Expected statement text: the test compares it with what the key-column migration hands a stand-in schema editor, which only records it; the test opens no database connection.",
    ),
    (
        "futureagi/tests/test_code_executor_results.py",
        "test_the_async_error_names_the_called_function",
        "2dbacb30ad2fc933382fd412986d2438cd297c4b31ada8fcf647171c2c1c4ec9",
    ): (
        "OUT_OF_SCOPE",
        "Error text a Python eval returns when its main() is async, compared with assertIn; it names the user's function, not a database statement, and the file has no database driver.",
    ),
    (
        "futureagi/tracer/migrations/0117_instant_eval_scale.py",
        "<module>",
        "61ef42089f6f0b1bf192b94d80fcbc18f55868e86144992097bbe4ccf3db9e37",
    ): (
        "OUT_OF_SCOPE",
        "Migration step that sets the Instant eval row table's auto-analyze thresholds, a storage option the migration executor applies; it reads and changes no row.",
    ),
    (
        "futureagi/tracer/migrations/0117_instant_eval_scale.py",
        "<module>",
        "82a34580c896b9b36cf7a30688374bfdd3dc09861ef9e7a6d5adac7010f5a9a8",
    ): (
        "OUT_OF_SCOPE",
        "Reverse migration step that resets the Instant eval row table's auto-analyze thresholds to the defaults; it reads and changes no row.",
    ),
    (
        "futureagi/tracer/services/clickhouse/schema.py",
        "<module>",
        "5484a68c2153c8abd1b6ce988bb8287b5db781aa33ca49a17839d31172f0ff6b",
    ): (
        "OUT_OF_SCOPE",
        "ClickHouse ReplicatedReplacingMergeTree destination schema for the agent definition CDC mirror, not application PostgreSQL.",
    ),
    (
        "futureagi/tracer/services/instant_eval_answers.py",
        "record_answer",
        "fd20724b7d4629e0c6a15832a53e2b0ce42029324b4d4a318ebb62bdd869ac4e",
    ): (
        "OUT_OF_SCOPE",
        "Reads one settled eval logger entry joined to its row in tracer_instant_eval_row; the interpolated label and confidence are fixed CASE expressions, values are query parameters, and the answer is written afterwards through the guarded ORM upsert, not by this statement.",
    ),
    (
        "futureagi/tracer/services/instant_eval_answers.py",
        "_copy",
        "863c1bfa6935c54c168480ef344f12a1be229bc923a794691cdf0bc0cd659ec3",
    ): (
        "OUT_OF_SCOPE",
        "Reads a batch of settled eval logger entries joined to their rows in tracer_instant_eval_row; the interpolated label, confidence, row id and settled condition are fixed module expressions, values are query parameters, and answers are written afterwards through the guarded ORM upsert.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "_user_rollup",
        "ee5cb8a66f4ed9bd1a9788e00d85126a8fcdffc645e48cd9de4bf1be99cfb889",
    ): (
        "OUT_OF_SCOPE",
        "Read-only WITH fragment over tracer_instant_eval_row and its answers that rolls a user question's sessions up to one row per user; every caller wraps it in a read statement and passes values as query parameters.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "_statement",
        "55878e5d2e6b58fb9f29d07de75afac13b892c4a7063c1ba228d4faa431d1bf1",
    ): (
        "OUT_OF_SCOPE",
        "Read-only WITH fragment over tracer_instant_eval_row and its answers that the run summary groups by time, user, model, turns and error; the interpolated pieces are built from fixed text and values are query parameters.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "users_by_answer",
        "d7de5e3b41bfb849e932e5b85ffbdd1464ffcce348340361000d7c4fd8632466",
    ): (
        "OUT_OF_SCOPE",
        "Reads tracer_instant_eval_row with its answers for the users picked as leaning to an answer, grouped per user; the interpolated joins and columns are built from fixed text and values are query parameters.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "_user_conditions",
        "44e65121482896b340b0cdc105d7cf70ed39b8f3ba14046fb522d32290dc7234",
    ): (
        "OUT_OF_SCOPE",
        "Read-only filter condition: an EXISTS subquery over tracer_instant_eval_row matching a user's label, id or excerpt, with the search text passed as query parameters.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "_consistent_condition",
        "7aae06ca871a81d43b5ee8aaba542924aa00f1e0c4a0d6fc18b3a646ebdcccbd",
    ): (
        "OUT_OF_SCOPE",
        "Read-only filter condition: an IN subquery over tracer_instant_eval_row and its answers picking the users k of whose n sessions got one answer; every value is a query parameter.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "rows_page",
        "4243a3665d82468ba2912e3e153116727abd6c6743a9ec713896b373874a875e",
    ): (
        "OUT_OF_SCOPE",
        "Reads one page of a run's rows from tracer_instant_eval_row in keyset order; the interpolated columns, joins, filter and order are built from fixed text and every value is a query parameter.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "rows_page",
        "8280c46b42bfe6b52afca42ef2be535a1f337ce395a3b11ea698b23389ef6ca3",
    ): (
        "OUT_OF_SCOPE",
        "Counts the rows of tracer_instant_eval_row that the rows page filter picks; the interpolated filter is built from fixed text and every value is a query parameter.",
    ),
    (
        "futureagi/tracer/services/instant_eval_summary.py",
        "iter_export",
        "4243a3665d82468ba2912e3e153116727abd6c6743a9ec713896b373874a875e",
    ): (
        "OUT_OF_SCOPE",
        "Reads the export's rows from tracer_instant_eval_row page by page in keyset order; the interpolated columns, joins, filter and order are built from fixed text and every value is a query parameter.",
    ),
    (
        "futureagi/agentcc/migrations/_integration_key_guard.py",
        "<module>",
        "f3b4595bc361da19974d1d039fc8c9263e464d336f7880b51cb7e8d1437f08e8",
    ): (
        "OUT_OF_SCOPE",
        "REFUSAL, the operator-facing message shown when INTEGRATION_ENCRYPTION_KEY is unset or made up; its 'update' and 'Set it' are prose, never passed to a cursor or executed, and no table is named.",
    ),
}
