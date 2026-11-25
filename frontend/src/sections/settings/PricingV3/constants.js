/**
 * Pricing page constants — feature labels, icons, descriptions.
 *
 * Feature keys use camelCase (matching the backend→frontend middleware conversion).
 * Labels are user-facing display names.
 */

// ── Feature Labels (camelCase key → display label) ──────────────────────

export const FEATURE_LABELS = {
  // Boolean features
  has_knowledge_base: "Knowledge Bases",
  has_review_workflow: "Review Workflow",
  has_agreement_metrics: "Agreement Metrics",
  has_required_labels: "Required Labels",
  has_audit_logs: "Audit Logs",
  has_voice_sim: "Voice Simulation",
  has_synthetic_data: "Synthetic Data Generation",
  has_agentic_eval: "Agentic Evaluation",
  has_optimization: "Optimization",
  has_scim: "SCIM Provisioning",
  has_project_rbac: "Project RBAC",
  has_custom_roles: "Custom Roles",
  has_data_masking: "Data Masking",
  has_oauth_sso: "OAuth SSO",
  has_saml_sso: "SAML SSO",
  // Numeric limits
  monitors: "Monitors & Alerts",
  alerts: "Alert Rules",
  queues: "Annotation Queues",
  shadow_experiments: "Shadow Experiments",
  automation_rules: "Automation Rules",
  knowledge_bases: "Knowledge Bases",
  max_concurrency: "Max Concurrency",
  gateway_email_alerts: "Gateway Email Alerts",
  gateway_webhooks: "Gateway Webhooks",
};

// ── Add-on Icons ────────────────────────────────────────────────────────

export const ADDON_ICONS = {
  boost: "solar:bolt-linear",
  scale: "solar:speedometer-middle-linear",
  enterprise: "solar:buildings-linear",
};

// ── Add-on Descriptions (incremental value over previous tier) ──────────

export const ADDON_DESCRIPTIONS = {
  boost: [
    "Knowledge bases",
    "Review workflows",
    "15 monitors & alerts",
    "90-day data retention",
  ],
  scale: [
    "Everything in Boost",
    "Unlimited resources",
    "SCIM provisioning",
    "Custom roles",
    "Optimization",
    "1-year data retention",
  ],
  enterprise: [
    "Everything in Scale",
    "Data masking",
    "Dedicated support",
    "7-year data retention",
    "Custom pricing",
  ],
};

// ── Feature Groups (for the comparison matrix) ──────────────────────────
// Keys must be camelCase (matching middleware conversion).
// type: "bool" | "numeric" | "custom"

export function getFeatureGroups(formatCompact) {
  return [
    {
      name: "Observability",
      features: [
        { label: "Traces & Spans", allPlans: true },
        { label: "Dashboards", allPlans: true },
        { label: "Monitors & Alerts", key: "monitors", type: "numeric" },
        { label: "Alert Rules", key: "alerts", type: "numeric" },
      ],
    },
    {
      name: "Evaluations",
      features: [
        { label: "Heuristic Evals (BYOK)", allPlans: true },
        {
          label: "Turing Evals (Managed AI)",
          key: "free_ai_credits",
          type: "custom",
          format: (v) =>
            v > 0 ? `${formatCompact(v)} credits/mo` : "Pay per use",
        },
        { label: "Agentic Evaluation", key: "has_agentic_eval", type: "bool" },
      ],
    },
    {
      name: "Simulation",
      features: [
        { label: "Voice Simulation", key: "has_voice_sim", type: "bool" },
        {
          label: "Synthetic Data Generation",
          key: "has_synthetic_data",
          type: "bool",
        },
      ],
    },
    {
      name: "Advanced Features",
      features: [
        { label: "Knowledge Bases", key: "has_knowledge_base", type: "bool" },
        { label: "Review Workflow", key: "has_review_workflow", type: "bool" },
        {
          label: "Agreement Metrics",
          key: "has_agreement_metrics",
          type: "bool",
        },
        { label: "Required Labels", key: "has_required_labels", type: "bool" },
        { label: "Project RBAC", key: "has_project_rbac", type: "bool" },
        { label: "Custom Roles", key: "has_custom_roles", type: "bool" },
        { label: "SCIM Provisioning", key: "has_scim", type: "bool" },
        { label: "Audit Logs", key: "has_audit_logs", type: "bool" },
        { label: "Data Masking", key: "has_data_masking", type: "bool" },
        { label: "Optimization", key: "has_optimization", type: "bool" },
        { label: "OAuth SSO", key: "has_oauth_sso", type: "bool" },
        { label: "SAML SSO", key: "has_saml_sso", type: "bool" },
      ],
    },
    {
      name: "Gateway",
      features: [
        {
          label: "Gateway Email Alerts",
          key: "gateway_email_alerts",
          type: "numeric",
        },
        { label: "Gateway Webhooks", key: "gateway_webhooks", type: "numeric" },
      ],
    },
    {
      name: "Limits & Rates",
      features: [
        { label: "Annotation Queues", key: "queues", type: "numeric" },
        {
          label: "Shadow Experiments",
          key: "shadow_experiments",
          type: "numeric",
        },
        {
          label: "Automation Rules",
          key: "automation_rules",
          type: "numeric",
        },
        { label: "Max Concurrency", key: "max_concurrency", type: "numeric" },
        {
          label: "API Rate Limit",
          key: "api_rate_rpm",
          type: "custom",
          format: (v) =>
            v === -1 ? "Unlimited" : `${formatCompact(v)} req/min`,
        },
      ],
    },
    {
      name: "Data Retention",
      features: [
        {
          label: "Trace Retention",
          key: "retention_traces_days",
          type: "custom",
          format: (v) =>
            v >= 365
              ? `${Math.round(v / 365)} year${v >= 730 ? "s" : ""}`
              : `${v} days`,
        },
        {
          label: "Gateway Log Retention",
          key: "retention_gateway_logs_days",
          type: "custom",
          format: (v) =>
            v >= 365
              ? `${Math.round(v / 365)} year${v >= 730 ? "s" : ""}`
              : `${v} days`,
        },
      ],
    },
  ];
}

// Keys to skip when building the dialog feature list
export const SKIP_FEATURE_PREFIXES = ["free", "retention", "api", "ingestion"];
