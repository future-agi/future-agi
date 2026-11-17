/** The COMPLETE public ALL_SCHEDULES identity/queue/action inventory at this
 * exact prep source, NOT a live query and NOT just the gateway's three alerts.
 * Provenance: futureagi/tfc/temporal/schedules/__init__.py:47-73; each group below
 * cites its leaf module. No Django imports (which would initialize services).
 * MCP revision/credential lists are empty at this pin, not omitted accidentally.
 * outbox_cdc.py selects TWO additional rows iff FI_CDC_MODE == outbox; require
 * the effective registration-process value from the runtime, never infer it
 * from a topology name. Any candidate schedule-source drift requires re-pinning.
 * Intervals/cron are not claimed by A02; telemetry/host-event intervals are
 * configuration-dependent. Do not invent a cadence from the topology.
 */
export const SOURCE_REF = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';
export type CdcMode = 'peerdb' | 'outbox';
export interface ScheduleIdentity {
  id: string;
  taskQueue: string;
  workflowType: string;
  activityName: string | null;
}
type Entry = readonly [id: string, activity: string, queue: string, workflow?: string];
const GROUPS: Record<string, readonly Entry[]> = {
  // All paths relative to futureagi/tfc/temporal/schedules/ at SOURCE_REF.
  'model_hub.py': [
    ['eval-evaluation', 'execute_evaluation', 'default'],
    ['error-localizer', 'error_localizer_task', 'default'],
    ['process-pending-row-tasks', 'process_pending_row_tasks', 'default'],
    ['optimization-runner', 'optimization_runner', 'tasks_l'],
    ['delete-unused-compare-folder', 'delete_unused_compare_folder', 'default'],
    ['recover-stuck-run-prompts', 'recover_stuck_run_prompts', 'default'],
    ['annotation-automation-rules', 'evaluate_due_automation_rules', 'default'],
    ['annotation-realtime-digest', 'send_annotation_realtime_digest_task', 'default'],
    ['annotation-daily-digest', 'send_annotation_daily_digest_task', 'default'],
  ],
  'tracer.py': [
    ['process-inline-evals', 'process_in_line_evals', 'tasks_s'],
    ['sweep-stranded-eval-tasks', 'sweep_stranded_eval_tasks', 'tasks_s'],
    ['check-alerts', 'check_alerts', 'tasks_l'],
    ['drain-feed-alerts', 'drain_feed_alerts', 'tasks_s'],
    ['run-evals-on-spans', 'run_evals_on_spans', 'tasks_s'],
    ['process-external-evals', 'process_external_evals', 'default'],
    ['fetch-observability-logs', 'fetch_observability_logs', 'tasks_s'],
  ],
  'simulate.py': [
    ['monitor-chat-test-executions', 'monitor_chat_test_executions', 'tasks_s'],
    ['monitor-chat-timeout-call-executions', 'monitor_chat_timeout_call_executions', 'tasks_s'],
    ['sweep-stuck-scoring', 'sweep_stuck_scoring', 'tasks_s'],
    ['process-prompt-based-chat-simulations', 'process_prompt_based_chat_simulations', 'tasks_s'],
    ['recover-hosted-harness-conversations', 'recover_hosted_harness_conversations', 'default'],
    ['seal-unsealed-hosted-harness-usage', 'seal_unsealed_hosted_harness_usage', 'default'],
    ['harness-github-poll', 'poll_harness_github_heads', 'default'],
    ['tick-harness-nudges', 'tick_harness_nudges', 'default'],
  ],
  'integrations.py': [
    ['sync-integrations', 'poll_active_integrations', 'tasks_s'],
    ['check-integration-errors', 'check_integration_error_alerts', 'tasks_s'],
  ],
  // Dedicated workflow types pinned additionally from tfc/temporal/billing/
  // workflows.py:MonthlyClosingWorkflow and marketplace/workflows.py:
  // GCPMarketplaceConsumerWorkflow (both @workflow.defn without name override).
  'billing.py': [
    ['budget-catchup', 'evaluate_budgets_catchup_activity', 'default'],
    ['monthly-closing', 'monthly_closing_activity', 'tasks_s', 'MonthlyClosingWorkflow'],
  ],
  'marketplace.py': [
    ['gcp-marketplace-consumer', 'drain_gcp_marketplace_events_activity', 'default', 'GCPMarketplaceConsumerWorkflow'],
    ['gcp-marketplace-usage-report', 'report_gcp_marketplace_usage_activity', 'default'],
    ['gcp-marketplace-plan-reconcile', 'reconcile_gcp_marketplace_plans_activity', 'default'],
    ['gcp-marketplace-usage-reconcile', 'reconcile_gcp_marketplace_usage_activity', 'default'],
  ],
  'retention.py': [
    ['retention-soft-delete', 'soft_delete_expired_data_activity', 'default'],
    ['retention-hard-delete', 'hard_delete_expired_data_activity', 'default'],
  ],
  'deployment_telemetry.py': [
    ['deployment-telemetry-heartbeat', 'send_deployment_telemetry_heartbeat', 'default'],
    ['deployment-telemetry-events', 'flush_deployment_telemetry_events', 'default'],
  ],
  'enterprise_heartbeat.py': [['enterprise-license-heartbeat', 'send_enterprise_heartbeat', 'default']],
  'omega_hub.py': [
    ['omega-hub-operations-check', 'omega_hub.check_operations', 'tasks_s'],
    ['omega-hub-outbox-drain', 'omega_hub.deliver_outbox', 'tasks_s'],
    ['omega-hub-retention', 'omega_hub.retention', 'tasks_s'],
    ['omega-hub-rules-notify', 'omega_hub.rules_notify', 'tasks_s'],
  ],
  'omega_host_events.py': [['omega-host-events-drain', 'drain_omega_host_events', 'tasks_s']],
  'agentcc_managed.py': [
    ['agentcc-managed-settlement-drain', 'drain_managed_settlements_activity', 'default'],
    ['agentcc-managed-push-sweep', 'sweep_unacked_pushes_activity', 'default'],
    ['agentcc-managed-gateway-reconcile', 'reconcile_managed_gateway_activity', 'default'],
  ],
  'agentcc_retention.py': [['agentcc-log-retention', 'purge_expired_agentcc_request_logs_activity', 'default']],
  'agentcc_mcp_revisions.py': [],
  'agentcc_mcp_credentials.py': [],
  'agentcc_alerts.py': [
    ['agentcc-alerts-evaluator', 'evaluate_agentcc_email_alerts_activity', 'default'],
    ['agentcc-webhook-delivery', 'deliver_agentcc_webhook_events_activity', 'default'],
  ],
  'agentcc_batches.py': [['agentcc-batches', 'poll_agentcc_batches_activity', 'default']],
  'saml.py': [['saml-purge-login-state', 'purge_login_state', 'default']],
  // webhooks/temporal/workflows.py declares these explicit @workflow.defn names.
  'webhooks.py': [
    ['webhooks-health', 'webhooks_health_pass', 'webhooks', 'WebhooksHealthWorkflow'],
    ['webhooks-retention', 'webhooks_retention_pass', 'webhooks', 'WebhooksRetentionWorkflow'],
  ],
};
const OUTBOX: readonly Entry[] = [
  ['outbox-cdc-drain', 'drain_outbox_cdc', 'tasks_s'],
  ['outbox-cdc-reconcile', 'reconcile_outbox_cdc', 'tasks_l'],
];

export function scheduleCatalog(cdcMode: CdcMode): ScheduleIdentity[] {
  if (cdcMode !== 'peerdb' && cdcMode !== 'outbox') throw new Error('Unbound effective FI_CDC_MODE');
  // manager.py:_build_schedule_for_config supplies no TaskRunnerInput when
  // workflow_class is set. activityName must then be null, not a guessed input.
  const entries = [...Object.values(GROUPS).flat(), ...(cdcMode === 'outbox' ? OUTBOX : [])];
  return entries.map(([id, activityName, taskQueue, workflowType]) => ({ id, taskQueue,
    workflowType: workflowType ?? 'TaskRunnerWorkflow', activityName: workflowType ? null : activityName,
  })).sort((a, b) => a.id.localeCompare(b.id));
}
