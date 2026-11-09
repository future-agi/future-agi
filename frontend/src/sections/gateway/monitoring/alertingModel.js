import { enqueueSnackbar } from "notistack";

import {
  ALERT_CHANNEL_TYPE_OPTIONS,
  ALERT_METRIC_OPTIONS,
} from "../constants/alerting";
import { isMaskedSecret } from "../components/SecretField";
import { EMPTY } from "../utils/formatters";

// Alert rules and channels as the gateway reads them. Two writers store two
// shapes (Settings > Alerting saves arrays; older configs keyed objects by
// name), so both are read. A rule's severity is pushed and the gateway sends
// it with every alert. A channel's `severity_filter` is a console-only key
// that config_push drops before the gateway sees it, so nothing here shows
// it. `enabled: false` keeps an entry out of the push.

function entries(config, keys) {
  const alerting = config?.alerting || config?.alerts || {};
  const raw = keys.map((k) => alerting[k]).find((v) => v != null) || [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw === "object") {
    return Object.entries(raw).map(([name, cfg]) => ({
      name,
      ...(typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

export const extractAlertRules = (config) =>
  entries(config, ["rules", "alert_rules"]);

export const extractChannels = (config) =>
  entries(config, ["channels", "notification_channels"]);

export const isOn = (entry) => entry?.enabled !== false;

const metricLabel = (metric) =>
  ALERT_METRIC_OPTIONS.find((m) => m.value === metric)?.label ||
  metric ||
  EMPTY;

export const channelTypeLabel = (type) =>
  ALERT_CHANNEL_TYPE_OPTIONS.find((t) => t.value === type)?.label ||
  type ||
  EMPTY;

/** "Error Count >= 5 over 5m" */
export function describeRule(rule) {
  const parts = [
    metricLabel(rule?.metric),
    rule?.condition || ">",
    rule?.threshold ?? EMPTY,
  ];
  const text = parts.join(" ");
  return rule?.window ? `${text} over ${rule.window}` : text;
}

/**
 * Scheme and host only: a webhook URL can carry its secret in the path. A
 * URL stored as a credential reads back as a mask: it says it is stored,
 * never shows the mask.
 */
export function maskUrl(url) {
  if (!url) return EMPTY;
  if (isMaskedSecret(url)) return "Stored, hidden";
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}/…`;
  } catch {
    return "(hidden)";
  }
}

/**
 * Report an alerting save honestly: Django answers 200 with
 * gateway_synced:false when the push to the gateway failed, which is
 * a warning, never a success.
 */
export function reportAlertingSaved(result, successMessage) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(
      result.gateway_warning ||
        "Saved, but the gateway did not take the change yet.",
      { variant: "warning" },
    );
    return;
  }
  enqueueSnackbar(successMessage, { variant: "success" });
}
