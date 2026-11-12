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
  // has_scim and has_project_rbac have no label on purpose: neither capability
  // is built, so neither is offered (see planFormatters).
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
// Each card bullet names the entitlement it stands for, so a card never offers
// what the Feature comparison crosses out for that add-on, nor something the
// plan it builds on already has. Numbers come from the add-on's own values.
// Bullets without a key are about the service, not a plan entitlement.

// The plan each add-on is bought on top of (one rank below on this page).
export const ADDON_BASE_PLAN = {
  boost: "payg",
  scale: "boost",
  enterprise: "scale",
};

const monitorsBullet = (v) =>
  v === -1
    ? "Unlimited monitors & alerts"
    : `${v.toLocaleString("en-US")} monitors & alerts`;

const retentionBullet = (v) => {
  if (v < 0) return null;
  return v >= 365
    ? `${Math.round(v / 365)}-year data retention`
    : `${v}-day data retention`;
};

export const ADDON_DESCRIPTIONS = {
  boost: [
    { key: "has_knowledge_base", label: "Knowledge bases" },
    { key: "has_review_workflow", label: "Review workflows" },
    { key: "monitors", format: monitorsBullet },
    { key: "retention_traces_days", format: retentionBullet },
  ],
  scale: [
    { label: "Everything in Boost" },
    { key: "monitors", format: monitorsBullet },
    { key: "has_custom_roles", label: "Custom roles" },
    { key: "has_optimization", label: "Optimization" },
    { key: "retention_traces_days", format: retentionBullet },
  ],
  enterprise: [
    { label: "Everything in Scale" },
    { key: "has_data_masking", label: "Data masking" },
    { label: "Dedicated support" },
    { key: "retention_traces_days", format: retentionBullet },
    { label: "Custom pricing" },
  ],
};

// The card bullets for one add-on: a flag bullet when the add-on has the flag
// and its base plan does not; a number bullet when the add-on has a value
// (Unlimited is -1) that differs from its base plan's.
export function getAddonBullets(addonKey, features, baseFeatures) {
  return (ADDON_DESCRIPTIONS[addonKey] || []).flatMap(
    ({ key, label, format }) => {
      if (!key) return [label];
      const value = features?.[key];
      const baseValue = baseFeatures?.[key];
      if (!format) return value === true && baseValue !== true ? [label] : [];
      if (typeof value !== "number" || value === 0 || value === baseValue) {
        return [];
      }
      const text = format(value);
      return text ? [text] : [];
    },
  );
}

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
        { label: "Custom Roles", key: "has_custom_roles", type: "bool" },
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
