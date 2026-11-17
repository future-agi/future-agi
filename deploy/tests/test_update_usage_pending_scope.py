"""Pinned 241/246 inventory and reviewed 45/50-scope regression, without Django.

The complete added-file inventory is pinned at frozen t60 (45dcab259ac0).
Only the preserved reviewed fixture runs the shipped touched()/inbound()
resolver against AST-derived operation and ModelState doubles; unrelated
new operations are not simulated. No migration module, model, RunPython,
SQL or app is executed. This is not native MigrationLoader or schema proof,
nor a substitute for the B scope probe or PostgreSQL constraint observation.
"""
import ast
import copy
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import rehearse_update_compatibility as p
from deploy.tests.test_update_usage_reference import ROOT, REVISIONS, source

# Preserve the original SQL/sampling/inbound-edge simulator fixture exactly.
REVIEWED_SCOPE_LATEST = (
    ('accounts','0029_demotracepublication_demoseedrequest'),
    ('accounts','0030_queue_existing_demo_trace_publications'),
    ('accounts','0031_orgapikey_coding_agent'),
    ('usage','0030_add_jev_evaluator_call_type'),
    ('tracer','0116_insight_reports'),
    ('tracer','0117_instant_eval_scale'),
    ('tracer','0118_instant_eval_answers'),
    ('tracer','0119_instant_eval_scope_preset'),
    ('tracer','0120_merge_error_feed_alerts_and_instant_eval'),
    ('tracer','0121_remove_insightconfig_schedule'),
    ('tracer','0122_merge_instant_eval_and_simulation_project'),
    ('tracer','0123_merge_instant_eval_and_shared_link_resource'),
    ('tracer','0124_merge_analysis_and_alert_high_water'),
    ('tracer','0125_insightrun_source_started_at'),
    ('tracer','0126_analysis_source_arrival'),
    ('tracer','0127_project_analysis_source_generation'),
    ('tracer','0128_instantevalrow_source_session_ids'),
    ('tracer','0129_project_analysis_source_generation_db_default'),
    ('tracer','0130_observationspan_internal_type'),
    ('simulate','0102_agent_fix_engine_notes'),
    ('simulate','0103_harness_job_verify_of'),
    ('tracer','0098_useralertmonitor_webhook_url'),
    ('tracer','0131_merge_alert_webhook'),
    ('tracer','0132_merge_alert_webhook_and_sql_workbench'),
    ('accounts','0026_orgapikey_expires_at'),
    ('accounts','0031_merge_api_key_expiry'),
    ('accounts','0032_merge_expiry_and_coding_agent'),
)
# Eight SQL additions shared with the SQL-only candidate. These identities
# account for the graph; they are not safety declarations for opaque operations.
SQL_ADDITIONS = (
    ('tracer','0117_sql_workbench'),
    ('tracer','0118_merge_sql_workbench_and_simulation_project'),
    ('tracer','0119_sql_execution_result_reuse'),
    ('tracer','0120_merge_sql_execution_and_shared_issue'),
    ('tracer','0121_merge_sql_execution_and_alert_high_water'),
    ('tracer','0122_sql_payload_expiry_index'),
    ('tracer','0130_merge_20261104_0940'),
    ('tracer','0131_merge_sql_workbench_and_observationspan_internal_type'),
)
# Ten more identities on the sampling layer, including its graph joins.
SAMPLING_ADDITIONS = (
    ('tracer','0117_cohort_sampling'),
    ('tracer','0117_sampling_project_db_default'),
    ('tracer','0119_merge_sampling_default_and_simulation_project'),
    ('tracer','0120_sampling_cohort_lease'),
    ('tracer','0121_merge_sampling_and_sql_reuse'),
    ('tracer','0122_merge_sampling_and_alert_issue_state'),
    ('tracer','0123_merge_20261104_0743'),
    ('tracer','0131_merge_20261104_1308'),
    ('tracer','0132_merge_sampling_and_sql_workbench_dev_merge'),
    ('tracer','0133_merge_sampling_and_alert_webhook'),
)
REVIEWED_SCOPE_LATEST += SQL_ADDITIONS + SAMPLING_ADDITIONS
FLOOR_ONLY = (
    ('simulate','0099_harness_versioning'),('simulate','0100_agent_fix'),
    ('simulate','0101_agent_fix_candidate_override'),
    ('tracer','0119_retire_deleted_eval_memberships'),
    ('tracer','0120_alert_issue_state_occurrences_high_water'),
)


# Full static added-file inventory, including helper/__init__ files matched by
# the git-diff guard. This is not a claim that every file is a Migration class.
# Re-pin deliberately when the shipping population changes; never derive this
# expected tuple from HEAD at test runtime.
LATEST = (
    ('accounts','0026_host_event_outbox'),
    ('accounts','0026_orgapikey_expires_at'),
    ('accounts','0026_orgapikey_secret_hash'),
    ('accounts','0027_seal_api_secrets'),
    ('accounts','0028_merge_20261019_1400'),
    ('accounts','0029_demotracepublication_demoseedrequest'),
    ('accounts','0029_omega_purge_lifecycle'),
    ('accounts','0030_merge_omega_purge_and_simulation_agent_key'),
    ('accounts','0030_queue_existing_demo_trace_publications'),
    ('accounts','0031_merge_api_key_expiry'),
    ('accounts','0031_orgapikey_coding_agent'),
    ('accounts','0031_retire_org_keys'),
    ('accounts','0032_merge_expiry_and_coding_agent'),
    ('accounts','0032_service_grants'),
    ('accounts','0033_batch_submission_intent'),
    ('accounts','0034_retirement_manifest'),
    ('accounts','0035_gateway_retirement_fence'),
    ('accounts','0036_authtoken_password_reset_type'),
    ('accounts','0037_merge_c26_dev_candidate'),
    ('accounts','0038_organizationmembership_removed_at'),
    ('accounts','0039_merge_c26_dev_1109'),
    ('accounts','0040_alter_user_invited_by'),
    ('accounts','0041_default_workspace_for_every_organization'),
    ('accounts','0042_merge_line_and_dev_1fc7d403'),
    ('accounts','0043_service_grant_console_run'),
    ('accounts','0044_service_grant_run_authorization'),
    ('agentcc','0029_org_config_managed_models'),
    ('agentcc','0030_request_log_v2'),
    ('agentcc','0031_request_log_v2_indexes'),
    ('agentcc','0032_request_attempt'),
    ('agentcc','0033_managed_upstream_key'),
    ('agentcc','0034_request_outcome'),
    ('agentcc','0035_mcp_credential'),
    ('agentcc','0036_mcp_tool_call'),
    ('agentcc','0037_mcp_tool_revision'),
    ('agentcc','0038_mcp_approval'),
    ('agentcc','0039_mcp_registry'),
    ('agentcc','0040_mcp_oauth'),
    ('agentcc','0041_mcp_indexes'),
    ('agentcc','0042_delegated_grant'),
    ('agentcc','0043_attempt_hollow_request_log_evidence'),
    ('agentcc','0044_memory_forget'),
    ('agentcc','0045_org_config_tools_identity'),
    ('agentcc','0046_org_config_db_defaults'),
    ('agentcc','0047_encrypt_org_config_secrets'),
    ('agentcc','0048_r7_batch_jobs'),
    ('agentcc','0048_r7_email_alert_state'),
    ('agentcc','0049_merge_r7'),
    ('agentcc','0050_encrypt_org_config_secret_aliases'),
    ('agentcc','0051_encrypt_alert_channel_urls'),
    ('agentcc','0052_auto_reload_consent'),
    ('agentcc','0052_cap_policy_activation'),
    ('agentcc','0052_mcp_service_provisioning'),
    ('agentcc','0053_cap_policy_revision'),
    ('agentcc','0053_ee_agent_key_policy'),
    ('agentcc','0054_ee_agent_key_expiry_backfill'),
    ('agentcc','0055_ee_agent_key_mint_idempotency'),
    ('agentcc','0056_mcp_secrets_to_vault'),
    ('agentcc','0057_mcp_credential_origin'),
    ('agentcc','0058_mcp_upstream_oauth'),
    ('agentcc','0058_retire_ee_credentials'),
    ('agentcc','0059_mcp_connect_org_client'),
    ('agentcc','0060_mcp_connect_shared_mode'),
    ('agentcc','0061_mcp_personal_principal_cleanup'),
    ('agentcc','0062_mcp_cleanup_receipts'),
    ('agentcc','0063_mcp_credential_grant_revision'),
    ('agentcc','0063_mcp_grant_org_only'),
    ('agentcc','0064_mcp_vendor_connections'),
    ('agentcc','0064_siem_export'),
    ('agentcc','0065_mcp_vendor_optimistic_lifecycle'),
    ('agentcc','0065_merge_mcp_adapters_siem'),
    ('agentcc','0065_siem_hec'),
    ('agentcc','0066_mcp_vendor_cleanup_obligations'),
    ('agentcc','0067_mcp_vendor_retained_receipts'),
    ('agentcc','0068_mcp_vendor_connect_leases'),
    ('agentcc','0069_merge_mcp_vendor_adapters_siem'),
    ('agentcc','0070_merge_mcp_line_siem_hec'),
    ('agentcc','0071_mcp_connect_cancellation_generation'),
    ('agentcc','0071_merge_request_log_consent_and_mcp'),
    ('agentcc','0072_api_key_ownership_immutable'),
    ('agentcc','0073_merge_708j_round3'),
    ('agentcc','0074_api_key_provisioning_state'),
    ('agentcc','0075_w11_approval_lifecycle'),
    ('agentcc','0075_webhook_credential_columns'),
    ('agentcc','0076_encrypt_webhook_credentials'),
    ('agentcc','0077_remove_webhook_plaintext'),
    ('agentcc','0078_historical_key_revocations'),
    ('agentcc','0078_merge_w11_webhook_credentials'),
    ('agentcc','0079_auto_reload_payment_method'),
    ('agentcc','0079_merge_r8c_revocation_webhooks'),
    ('agentcc','0080_auto_reload_arming'),
    ('agentcc','0081_auto_reload_intent_exclusion'),
    ('agentcc','0082_auto_reload_credit_optional_threshold'),
    ('agentcc','0083_managed_credit_checkout_binding'),
    ('agentcc','0084_checkout_actor_identifier'),
    ('agentcc','0085_dispatch_claim'),
    ('agentcc','0086_merge_dispatch_claim_webhook_at_rest'),
    ('agentcc','0087_mcp_credential_oauth_resource'),
    ('agentcc','0088_mcp_shared_connect_generation'),
    ('agentcc','0089_normalize_two_key_org_period'),
    ('agentcc','0090_request_log_error_code'),
    ('agentcc','0091_request_log_error_source'),
    ('agentcc','0092_console_run_signer_key'),
    ('agentcc','_integration_key_guard'),
    ('agentcc','_webhook_credentials_v1'),
    ('falcon_ai','0010_encrypt_connector_credentials'),
    ('falcon_ai','0011_mcpconnector_credential_generation'),
    ('falcon_ai','_builtin_skills'),
    ('mcp_server','0005_personal_key_reconnect'),
    ('mcp_server','0006_oauth_access_revocation'),
    ('mcp_server','0007_reconnect_required_db_default'),
    ('model_hub','0123_datasetversion_datasetversionitem_and_more'),
    ('model_hub','0123_promptlabelhead'),
    ('model_hub','0123_usereval_is_experiment_default'),
    ('model_hub','0124_dataset_description_tags'),
    ('model_hub','0124_dataset_saved_view'),
    ('model_hub','0124_datasetversionitem_unique_dataset_version_external_id'),
    ('model_hub','0124_evaltemplate_eval_type_dag'),
    ('model_hub','0124_experiment_run_control'),
    ('model_hub','0124_experiment_run_scope'),
    ('model_hub','0124_score_value_updated_at'),
    ('model_hub','0124_widen_model_hub_key_columns'),
    ('model_hub','0125_backfill_score_value_updated_at'),
    ('model_hub','0125_dataset_splits'),
    ('model_hub','0125_dataset_stars'),
    ('model_hub','0125_encrypt_model_hub_keys_at_rest'),
    ('model_hub','0125_experiment_run_control_phase'),
    ('model_hub','0125_experiment_run_mvp'),
    ('model_hub','0125_experiment_run_policy'),
    ('model_hub','0125_generated_input_snapshot'),
    ('model_hub','0126_dataset_folders'),
    ('model_hub','0126_evaltemplate_external_eval_type'),
    ('model_hub','0126_experiment_row_budget'),
    ('model_hub','0126_merge_dsx_works_705_r1'),
    ('model_hub','0126_row_source'),
    ('model_hub','0127_experimentrun_ordinal_datasetbaseline'),
    ('model_hub','0127_merge_dsx_works_705_r4'),
    ('model_hub','0127_merge_scope_and_run_control'),
    ('model_hub','0128_merge_keys_at_rest_and_experiment_runs'),
    ('model_hub','0128_merge_scope_and_splits'),
    ('model_hub','0129_merge_prompt_label_heads_and_experiment_runs'),
    ('model_hub','0130_merge_dsx_and_integration'),
    ('model_hub','0134_merge_dataset_works_and_integration'),
    ('model_hub','0135_evaltemplate_eval_type_external_and_dag'),
    ('model_hub','0136_merge_708j_round3'),
    ('model_hub','0137_merge_dsx3e_grid_snapshot'),
    ('model_hub','0137_merge_score_value_clock_and_708j_round3'),
    ('model_hub','0138_merge_dsx3e_c7_and_grid_snapshot'),
    ('model_hub','0138_merge_dsx3e_pgrid_row_source'),
    ('model_hub','0139_merge_dsx3f_c7_and_pgrid'),
    ('model_hub','0140_rebackfill_queueitem_project_upgraded'),
    ('model_hub','0141_native_experiment_run_bindings'),
    ('model_hub','0142_experiment_description'),
    ('model_hub','0143_row_activity'),
    ('model_hub','0144_workspace_tags'),
    ('omega_hub','0001_initial'),
    ('omega_hub','0002_version_strict_semver'),
    ('omega_hub','0003_erasure_and_email_verification'),
    ('omega_hub','0003_hub_operations'),
    ('omega_hub','0004_queue_age_and_bundle_locks'),
    ('omega_hub','0005_merge_hub_operations_and_erasure'),
    ('omega_hub','0006_checker_presence'),
    ('omega_hub','__init__'),
    ('omega_rollout','0001_initial'),
    ('omega_rollout','__init__'),
    ('simulate','0100_sandbox_harness_job'),
    ('simulate','0102_agent_definition_encrypted_api_key'),
    ('simulate','0102_agent_fix_engine_notes'),
    ('simulate','0102_prompt_optimizer_tenant_lineage'),
    ('simulate','0103_encrypt_agent_definition_api_key'),
    ('simulate','0103_harness_job_verify_of'),
    ('simulate','0103_merge_sandbox_harness_job'),
    ('simulate','0104_remove_agent_definition_api_key'),
    ('simulate','0105_merge_api_key_at_rest_tenant_lineage'),
    ('simulate','0106_merge_c25_credentials_agent_fix_notes'),
    ('simulate','0107_merge_r8c_r3a_leaves'),
    ('simulate','0108_merge_c26_dev_1109'),
    ('simulate','0109_agent_fix_model_reserved_usd'),
    ('simulate','0110_seed_system_personas'),
    ('simulate','0111_harness_versioning_backfill_state'),
    ('tracer','0098_useralertmonitor_webhook_url'),
    ('tracer','0115_omega_deployment_observe_link'),
    ('tracer','0116_insight_reports'),
    ('tracer','0117_cohort_sampling'),
    ('tracer','0117_instant_eval_scale'),
    ('tracer','0117_sampling_project_db_default'),
    ('tracer','0117_sql_workbench'),
    ('tracer','0118_instant_eval_answers'),
    ('tracer','0118_merge_omega_observe_link_and_error_feed'),
    ('tracer','0118_merge_sql_workbench_and_simulation_project'),
    ('tracer','0119_instant_eval_scope_preset'),
    ('tracer','0119_merge_sampling_default_and_simulation_project'),
    ('tracer','0119_omega_agent_observe_link'),
    ('tracer','0119_sql_execution_result_reuse'),
    ('tracer','0120_merge_error_feed_alerts_and_instant_eval'),
    ('tracer','0120_merge_sharedlink_and_agent_observe'),
    ('tracer','0120_merge_sql_execution_and_shared_issue'),
    ('tracer','0120_sampling_cohort_lease'),
    ('tracer','0121_background_trace_analysis_state'),
    ('tracer','0121_merge_sampling_and_sql_reuse'),
    ('tracer','0121_merge_sql_execution_and_alert_high_water'),
    ('tracer','0121_remove_insightconfig_schedule'),
    ('tracer','0122_merge_instant_eval_and_simulation_project'),
    ('tracer','0122_merge_sampling_and_alert_issue_state'),
    ('tracer','0122_sql_payload_expiry_index'),
    ('tracer','0123_merge_20261104_0743'),
    ('tracer','0123_merge_instant_eval_and_shared_link_resource'),
    ('tracer','0124_merge_analysis_and_alert_high_water'),
    ('tracer','0125_insightrun_source_started_at'),
    ('tracer','0126_analysis_source_arrival'),
    ('tracer','0127_project_analysis_source_generation'),
    ('tracer','0128_instantevalrow_source_session_ids'),
    ('tracer','0129_project_analysis_source_generation_db_default'),
    ('tracer','0130_merge_20261104_0940'),
    ('tracer','0130_merge_c26_dev_candidate'),
    ('tracer','0130_observationspan_internal_type'),
    ('tracer','0131_merge_20261104_1308'),
    ('tracer','0131_merge_alert_webhook'),
    ('tracer','0131_merge_c26_dev_1109'),
    ('tracer','0131_merge_sql_workbench_and_observationspan_internal_type'),
    ('tracer','0132_merge_alert_webhook_and_sql_workbench'),
    ('tracer','0132_merge_sampling_and_sql_workbench_dev_merge'),
    ('tracer','0132_seed_alert_issue_occurrences_high_water'),
    ('tracer','0133_merge_sampling_and_alert_webhook'),
    ('tracer','0133_useralertmonitor_threshold_rule'),
    ('tracer','0134_convert_provider_error_rate_thresholds'),
    ('tracer','0135_merge_sql_workbench_and_provider_error_rate_thresholds'),
    ('tracer','0136_merge_line_and_dev_1fc7d403'),
    ('usage','0030_add_jev_evaluator_call_type'),
    ('usage','0030_managed_credit_ledger'),
    ('usage','0031_ledger_phase2a'),
    ('usage','0032_ledger_opening_buckets'),
    ('usage','0033_backfill_topup_payment_intent'),
    ('usage','0034_merge_c26_dev_candidate'),
    ('usage','0035_usagebudget_threshold_value_help_text'),
    ('webhooks','0001_initial'),
    ('webhooks','0002_api'),
    ('webhooks','0002_operations'),
    ('webhooks','0003_merge'),
    ('webhooks','0004_delivery_endpoint_created'),
    ('webhooks','__init__'),
)


def keywords(node): return {k.arg:k.value for k in node.keywords}


class CharField(SimpleNamespace):
    """AST-only built-in-field double; constructor equality is not SQL proof."""
    non_db_attrs = ('choices',)

    def deconstruct(self):
        return None, 'django.db.models.CharField', self.arguments, dict(self.options)


def field(node):
    assert isinstance(node,ast.Call)
    options=keywords(node)
    kind=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id
    remote=None
    if kind in ('ForeignKey','OneToOneField','ManyToManyField'):
        target=options.get('to') or (node.args[0] if node.args else None)
        assert target is not None
        if isinstance(target,ast.Attribute):
            assert ast.unparse(target)=='settings.AUTH_USER_MODEL'
            target='accounts.user'
        else: target=ast.literal_eval(target)
        remote=SimpleNamespace(model=target,through=ast.literal_eval(options['through']) if 'through' in options else None)
    cls=CharField if kind=='CharField' else SimpleNamespace
    return cls(remote_field=remote,many_to_many=kind=='ManyToManyField',
        primary_key=ast.literal_eval(options['primary_key']) if 'primary_key' in options else False,
        unique=ast.literal_eval(options['unique']) if 'unique' in options else kind=='OneToOneField',
        db_index=ast.literal_eval(options['db_index']) if 'db_index' in options else False,
        db_table=ast.literal_eval(options['db_table']) if 'db_table' in options else None,
        arguments=[ast.dump(arg) for arg in node.args],
        options={key:ast.dump(value) for key,value in options.items()})


def model(node):
    options=keywords(node)
    declared={ast.literal_eval(k):v for k,v in zip(options.get('options',ast.Dict(keys=[],values=[])).keys,
        options.get('options',ast.Dict(keys=[],values=[])).values)}
    return SimpleNamespace(options=({'db_table':ast.literal_eval(declared['db_table'])} if 'db_table' in declared else {}),
        fields={ast.literal_eval(pair.elts[0]):field(pair.elts[1]) for pair in options['fields'].elts})


class State:
    def __init__(self): self.models={}
    def clone(self): return copy.deepcopy(self)


class Operation:
    def __init__(self,node):
        self.node=node
        options=keywords(node)
        for key in ('name','model_name','old_name','new_name'):
            if key in options: setattr(self,key,ast.literal_eval(options[key]))
        if 'field' in options: self.field=field(options['field'])
        if type(self).__name__=='RunPython': self.code=object()  # opaque; never called
        if type(self).__name__=='RunSQL': self.sql='opaque'  # only declaration is read
    def state_forwards(self,app,state):
        kind=type(self).__name__
        if kind=='CreateModel': state.models[(app,self.name.lower())]=model(self.node)
        elif kind in ('AddField','AlterField'): state.models[(app,self.model_name)].fields[self.name]=self.field
        elif kind=='RemoveField': state.models[(app,self.model_name)].fields.pop(self.name,None)
        elif kind=='RenameField':
            fields=state.models[(app,self.model_name)].fields
            fields[self.new_name]=fields.pop(self.old_name)
        else:
            # All remaining operations in these reviewed plans affect catalog
            # indexes/constraints or carry declarations; no state table rename.
            assert kind in ('RunPython','RunSQL','AddIndex','AddIndexConcurrently','RemoveIndex','AddConstraint')


def operation_types():
    kinds=('CreateModel','AddField','AlterField','RemoveField','RenameField','AddIndex',
        'RemoveIndex','AddConstraint','RunPython','RunSQL','SeparateDatabaseAndState')
    result={kind:type(kind,(Operation,),{'__module__':'django.db.migrations.operations.fixtures'}) for kind in kinds}
    result['AddIndexConcurrently']=type('AddIndexConcurrently',(result['AddIndex'],),
        {'__module__':'django.contrib.postgres.operations'})
    result['RunPython'].noop=object(); result['RunSQL'].noop=''
    return SimpleNamespace(**result)


def scope_environment(state):
    def resolve(remote,app,model_name):
        if remote=='self': return app,model_name.lower()
        if '.' in remote:
            app,remote=remote.split('.',1)
        return app,remote.lower()
    env=dict(ast=ast,re=re,migrations=operation_types(),models=SimpleNamespace(CharField=CharField),
        schemas={'postgres':'public','clickhouse':'default'},
        resolve_relation=resolve,strip_quotes=lambda s:s.replace('"',''),truncate_name=lambda s,length:s[:length])
    # Use the actual resolver's functions/constants, with imports and final
    # loader invocation left to the native test. Opaque declarations are AST data.
    nodes=[n for n in ast.parse(p.PENDING_TABLE_SCOPE).body if
        isinstance(n,ast.FunctionDef) and n.name!='declared' or
        isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('IDENT','NO_TABLE','MODEL_OPS') for t in n.targets)]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<shipping-table-scope>','exec'),env)
    def declared(migration):
        assert migration.declaration is not None, 'opaque_operation_table_scope_unavailable'
        return {env['resolved'](*v.split(':',1)) for v in migration.declaration['tables']}
    env['declared']=declared
    return env


def migration_path(app,name):
    return ROOT/'futureagi'/('ee/usage' if app=='usage' else app)/'migrations'/(name+'.py')


def baseline_state(revision):
    state=State()
    # Model table options/initial fields are read from real CreateModel ASTs.
    # Only usage's pre-AlterField inbound edges/uniqueness are relevant among
    # baseline fields in these plans; replace them with exact source model ASTs
    # below rather than deriving that key fact from the target's added choices.
    for path in sorted((ROOT/'futureagi').glob('**/migrations/*.py')):
        app=path.parent.parent.name
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and
                    isinstance(node.func.value,ast.Name) and node.func.value.id=='migrations' and node.func.attr=='CreateModel'): continue
            options=keywords(node)
            if 'name' not in options or 'fields' not in options: continue
            state.models[(app,ast.literal_eval(options['name']).lower())]=model(node)
    usage=ast.parse(source(revision,'ee/usage/models/usage.py'))
    for node in usage.body:
        if not isinstance(node,ast.ClassDef): continue
        fields={}
        for item in node.body:
            if not (isinstance(item,ast.Assign) and isinstance(item.value,ast.Call) and
                    isinstance(item.value.func,ast.Attribute) and isinstance(item.value.func.value,ast.Name)
                    and item.value.func.value.id=='models'): continue
            call=copy.deepcopy(item.value)
            # Source model imports are classes; normalize only real FK targets.
            if call.func.attr in ('ForeignKey','OneToOneField') and isinstance(call.args[0],ast.Name):
                name=call.args[0].id
                call.args[0]=ast.Constant(('accounts.' if name in ('Organization','Workspace','User') else 'usage.')+name.lower())
            fields[item.targets[0].id]=field(call)
        if fields:
            state.models[('usage',node.name.lower())]=SimpleNamespace(options={},fields=fields)
    return state


def pending_plan(identities,env):
    migrations={}
    for app,name in identities:
        tree=ast.parse(migration_path(app,name).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Migration')
        values={n.targets[0].id:n.value for n in cls.body if isinstance(n,ast.Assign)}
        dependencies=[ast.literal_eval(n) for n in values['dependencies'].elts if isinstance(n,ast.Tuple)]
        operations=[]
        for node in values['operations'].elts:
            if isinstance(node.func,ast.Name):
                # The SQL migration imports this standard Django operation by
                # name. Do not accept arbitrary custom operations as AddIndex.
                assert node.func.id=='AddIndexConcurrently'
                assert any(isinstance(item,ast.ImportFrom) and item.module=='django.contrib.postgres.operations'
                    and any(alias.name=='AddIndexConcurrently' and alias.asname is None for alias in item.names)
                    for item in tree.body)
                kind=node.func.id
            else:
                assert isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name)
                assert node.func.value.id=='migrations'
                kind=node.func.attr
            operations.append(getattr(env['migrations'],kind)(node))
        declaration=next((ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
            any(isinstance(t,ast.Name) and t.id=='UPDATE_SAFETY' for t in n.targets)),None)
        migrations[(app,name)]=SimpleNamespace(app_label=app,name=name,operations=operations,dependencies=dependencies,declaration=declaration)
    ordered=[]
    while migrations:
        ready=sorted(key for key,m in migrations.items() if not set(m.dependencies)&set(migrations))
        assert ready, 'cyclic pending fixture'
        for key in ready: ordered.append(migrations.pop(key))
    return ordered


class CompleteUsagePendingIntersectionTests(unittest.TestCase):
    def assert_complete_inventory(self,revision,identities,count):
        # Additions, not edited applied migrations. This deliberately breaks if
        # the shipping plan changes without refreshing its complete inventory.
        paths=subprocess.check_output(['git','-C',str(ROOT),'diff','--name-only','--diff-filter=A',
            revision,'HEAD','--','futureagi/**/migrations/*.py'],text=True).splitlines()
        actual={(Path(path).parent.parent.name,Path(path).stem) for path in paths}
        self.assertEqual(actual,set(identities)); self.assertEqual(len(actual),count)
        self.assertEqual(len(identities),count)  # duplicate fixture identities also fail

    def test_complete_latest_and_floor_inventory(self):
        self.assertEqual(len(SQL_ADDITIONS),8)
        self.assertEqual(len(SAMPLING_ADDITIONS),10)
        self.assertTrue(set(SQL_ADDITIONS).isdisjoint(SAMPLING_ADDITIONS))
        for revision,identities,count in ((REVISIONS[1],LATEST,241),(REVISIONS[0],LATEST+FLOOR_ONLY,246)):
            with self.subTest(revision=revision):
                self.assert_complete_inventory(revision,identities,count)

    def test_pending_inventory_rejects_unknown_missing_and_substituted_migrations(self):
        # Preserve the SQL-layer negative controls when adding sampling's graph.
        # Same-count replacement must fail just as an extra or missing file does.
        unknown = 'futureagi/tracer/migrations/9999_unreviewed_migration.py'
        for revision, identities, count in ((REVISIONS[1], LATEST, 241),
                                            (REVISIONS[0], LATEST + FLOOR_ONLY, 246)):
            paths = subprocess.check_output(['git', '-C', str(ROOT), 'diff',
                '--name-only', '--diff-filter=A', revision, 'HEAD', '--',
                'futureagi/**/migrations/*.py'], text=True).splitlines()
            self.assert_complete_inventory(revision, identities, count)
            for app, name in SQL_ADDITIONS + SAMPLING_ADDITIONS:
                missing = 'futureagi/' + app + '/migrations/' + name + '.py'
                self.assertIn(missing, paths)
                without = [path for path in paths if path != missing]
                for mutation, changed in (('missing', without),
                        ('unknown', paths + [unknown]),
                        ('same_count_substitution', without + [unknown])):
                    with self.subTest(revision=revision, missing=missing, mutation=mutation):
                        with mock.patch.object(subprocess, 'check_output',
                                return_value='\n'.join(changed) + '\n'):
                            with self.assertRaises(AssertionError):
                                self.assert_complete_inventory(revision, identities, count)

    def test_sql_index_operations_have_explicit_state_table_scope(self):
        # The sampling layer retains SQL's concurrent-index scope regression.
        state = baseline_state(REVISIONS[1])
        env = scope_environment(state)
        migration = pending_plan([('tracer', '0122_sql_payload_expiry_index')], env)[0]
        self.assertEqual([type(op).__name__ for op in migration.operations],
                         ['AddIndexConcurrently', 'AddIndexConcurrently', 'RunPython'])
        for op in migration.operations[:2]:
            self.assertEqual(env['touched'](op, migration, state),
                             {('postgres', 'public.tracer_sqlexecution')})
        # This does not authorize the separate undeclared RunPython operation.

    def test_unexpected_migration_still_fails_complete_inventory(self):
        paths=subprocess.check_output(['git','-C',str(ROOT),'diff','--name-only','--diff-filter=A',
            REVISIONS[1],'HEAD','--','futureagi/**/migrations/*.py'],text=True)
        with mock.patch.object(subprocess,'check_output',return_value=paths+
                'futureagi/tracer/migrations/9999_unexpected.py\n'):
            with self.assertRaises(AssertionError):
                self.assert_complete_inventory(REVISIONS[1],LATEST,241)

    def test_sampling_layer_has_exact_table_scopes(self):
        expected={
            '0117_cohort_sampling': {'public.tracer_project','public.tracer_eval_task',
                'public.tracer_eval_sampling_cohort','public.tracer_eval_sampling_member'},
            # The default ships in 0117_cohort_sampling; this AlterField is
            # identical, so Django emits no SQL and it touches no table.
            '0117_sampling_project_db_default': set(),
            '0119_merge_sampling_default_and_simulation_project': set(),
            '0120_sampling_cohort_lease': {'public.tracer_eval_sampling_cohort'},
            '0121_merge_sampling_and_sql_reuse': set(),
            '0122_merge_sampling_and_alert_issue_state': set(),
            '0123_merge_20261104_0743': set(),
            '0131_merge_20261104_1308': set(),
            '0132_merge_sampling_and_sql_workbench_dev_merge': set(),
            '0133_merge_sampling_and_alert_webhook': set(),
        }
        for revision in REVISIONS:
            state=baseline_state(revision); env=scope_environment(state)
            actual={}
            for migration in pending_plan(SAMPLING_ADDITIONS,env):
                tables=set()
                for op in migration.operations:
                    tables |= env['touched'](op,migration,state)
                    op.state_forwards(migration.app_label,state)
                self.assertTrue(all(engine=='postgres' for engine,table in tables))
                actual[migration.name]={table for engine,table in tables}
            with self.subTest(revision=revision):
                self.assertEqual(actual,expected)

    def test_reviewed_latest_and_floor_scope_including_unique_lookup_inbound_edges(self):
        """Guard the full inventory, then simulate only the reviewed 45/50 fixture."""
        registry=p.load_high_volume_registry()
        for revision,identities,count,reviewed,reviewed_count in (
                (REVISIONS[1],LATEST,241,REVIEWED_SCOPE_LATEST,45),
                (REVISIONS[0],LATEST+FLOOR_ONLY,246,REVIEWED_SCOPE_LATEST+FLOOR_ONLY,50)):
            with self.subTest(revision=revision):
                self.assert_complete_inventory(revision,identities,count)
                self.assertEqual(len(reviewed),reviewed_count)
                self.assertEqual(len(set(reviewed)),reviewed_count)
                self.assertTrue(set(reviewed).issubset(identities))
                state=baseline_state(revision); env=scope_environment(state)
                plan=pending_plan(reviewed,env); scopes=[]
                for migration in plan:
                    tables=set()
                    for op in migration.operations:
                        tables |= env['touched'](op,migration,state)
                        op.state_forwards(migration.app_label,state)
                    scopes.append(dict(app_label=migration.app_label,name=migration.name,
                        tables=[dict(engine=e,table=t) for e,t in sorted(tables)]))
                identities_in_order=[dict(app_label=m.app_label,name=m.name) for m in plan]
                selected=p.select_reference_registry(registry,scopes,plan=identities_in_order)
                expected={'tracer_trace','tracer_observation_span','tracer_eval_task','usage_apicalllog'}
                if revision==REVISIONS[0]: expected|={'tracer_error_cluster_traces','tracer_eval_logger','tracer_trace_error_group'}
                self.assertEqual({row['table'] for row in selected},expected)
                self.assertTrue(all(row['database']=='postgres' for row in selected))
                self.assertFalse(any(row.get('app_label')=='prism' or row.get('model') in ('UsageEventLog','UsageSummary') for row in selected))
                usage=next(row for row in scopes if row['app_label']=='usage')
                self.assertEqual({row['table'] for row in usage['tables']},
                    {'public.usage_apicalltype','public.usage_apicalllog','public.usage_pricing','public.usage_ratelimit'})
                for row in scopes:
                    scoped=p.select_reference_registry(registry,[row],plan=[{k:row[k] for k in ('app_label','name')}])
                    names={item['table'] for item in scoped}
                    if row['name']=='0030_queue_existing_demo_trace_publications': expected_row={'tracer_trace','tracer_observation_span'}
                    elif row['app_label']=='usage': expected_row={'usage_apicalllog'}
                    elif row['name'] in ('0118_instant_eval_answers','0117_cohort_sampling'): expected_row={'tracer_eval_task'}
                    elif row['name']=='0130_observationspan_internal_type': expected_row=set()  # Choices-only state change.
                    elif row['name']=='0119_retire_deleted_eval_memberships': expected_row={'tracer_error_cluster_traces','tracer_eval_logger','tracer_trace_error_group'}
                    else: expected_row=set()
                    self.assertEqual(names,expected_row,(row['app_label'],row['name']))

    def test_unique_name_is_the_non_pk_reason_usage_is_selected(self):
        state=baseline_state(REVISIONS[1]); env=scope_environment(state)
        migration=pending_plan([('usage','0030_add_jev_evaluator_call_type')],env)[0]
        op=migration.operations[0]
        self.assertFalse(op.field.primary_key); self.assertTrue(op.field.unique)
        self.assertIn(('postgres','public.usage_apicalllog'),env['touched'](op,migration,state))
        # Negative control removes BOTH before/after unique flags. The actual
        # source and target keep them; do not "fix" production by changing this.
        state.models[('usage','apicalltype')].fields['name'].unique=False
        op.field.unique=False
        # Retain a real column alteration: without one this becomes choices-only,
        # which now correctly has no physical-table scope at all.
        op.field.options['max_length']=ast.dump(ast.Constant(51))
        self.assertEqual(env['touched'](op,migration,state),{('postgres','public.usage_apicalltype')})
