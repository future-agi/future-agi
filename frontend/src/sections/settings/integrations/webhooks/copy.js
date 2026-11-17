// The Webhooks section's words (DESIGN.md section 11.2). One place, so the
// list, the endpoint page and the e2e flows agree on every string.
import { format, formatDistanceToNowStrict } from "date-fns";

export const EMPTY_STATE =
  "No webhooks yet. Send prompt and deployment events to your own HTTPS endpoint, signed so you can check they came from Future AGI.";

export const HTTP_EMPTY_STATE =
  "No webhooks yet. Send prompt and deployment events to your own HTTPS or HTTP endpoint, signed so you can check they came from Future AGI.";

export const HTTP_URL_HINT =
  "HTTPS or HTTP. Host and port restrictions still apply.";

export const HTTPS_RESTRICTED_URL_HINT =
  "HTTPS only. Host and port restrictions still apply.";

export const URL_HINT =
  "HTTPS only. Private, local and cloud metadata addresses are blocked.";

// Missing profile data fails closed for HTTP support and makes no private-host claim.
export const urlHint = (policy) =>
  policy?.http_allowed === true
    ? HTTP_URL_HINT
    : policy?.private_addresses_blocked === true
      ? URL_HINT
      : HTTPS_RESTRICTED_URL_HINT;

// A saved URL whose query values the API masks (url_display, "?token=…"):
// it cannot be sent back, so the Settings field starts empty.
export const URL_KEPT_HINT =
  "Its query values are hidden. Type the full URL to change it, or leave this empty to keep it.";

export const QUERY_TOKEN_WARNING =
  "This URL carries a value in its query string. Query strings end up in proxy and server logs; send a token in a header instead.";

export const HEADERS_HINT =
  "Sent exactly as typed with every request, for example an Authorization header your gateway wants. Every request is also signed.";

export const RUN_TARGETING_HINT =
  "A run that passes this webhook's id or URL gets its events sent here, even without a deployment subscription.";

export const FULL_PAYLOAD_OFF =
  "Your organization doesn't allow prompt text in webhooks. An org admin can change this.";

export const FULL_PAYLOAD_HINT =
  "Your receiver can always fetch the full prompt with the API.";

export const SECRET_ONCE =
  "Copy this signing secret now. You won't see it again; if you lose it, rotate it.";

export const ORG_WIDE_ROW = "Org-wide · also receives this workspace's events";

// Session replays (PostHog, Mixpanel) never record what carries a secret: the
// signing secret shown once, a URL that may hold a token in its query string,
// header values in the request preview. "ph-no-capture" makes PostHog's
// recorder block the element; "sensitive" is the record_mask_text_class FA
// gives Mixpanel (src/utils/Mixpanel/mixpanel.js).
export const NO_REPLAY = "ph-no-capture sensitive";

// The catalog in the order the dialog shows it, plain words first (section 11.2).
export const EVENT_FAMILIES = [
  {
    id: "prompts",
    label: "Prompts",
    events: [
      { type: "prompt.version.created", label: "A new version is committed" },
      {
        type: "prompt.label.changed",
        label: "A label is assigned, moved or removed",
      },
    ],
  },
  {
    id: "deployments",
    label: "Deployments",
    events: [
      { type: "deployment.run.completed", label: "A run completes" },
      {
        type: "deployment.run.failed",
        label: "A run fails or is interrupted",
      },
    ],
  },
];

export const EVENT_LABELS = Object.fromEntries(
  EVENT_FAMILIES.flatMap((f) => f.events.map((e) => [e.type, e.label])),
);

export const LABEL_ACTIONS = [
  { value: "assigned", label: "Assigned" },
  { value: "moved", label: "Moved" },
  { value: "removed", label: "Removed" },
];
export const ENVIRONMENTS = [
  { value: "production", label: "Production" },
  { value: "preview", label: "Preview" },
];
export const TRIGGER_KINDS = [
  { value: "api", label: "API" },
  { value: "cron", label: "Cron" },
  { value: "studio", label: "Studio" },
  { value: "batch", label: "Batch" },
];
export const FAILURE_STATUSES = [
  { value: "failed", label: "Failed", byDefault: true },
  { value: "interrupted", label: "Interrupted", byDefault: true },
  { value: "cancelled", label: "Cancelled", byDefault: false },
];

export const OVERLAP_CHOICES = [
  { seconds: 0, label: "no time (stop it now)" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 86400, label: "24 hours" },
  { seconds: 259200, label: "3 days" },
  { seconds: 604800, label: "7 days" },
];

export const hasQueryValue = (url) => {
  const q = String(url || "")
    .split("#")[0]
    .split("?")[1];
  return !!q && q.split("&").some((part) => part.split("=")[1]);
};

const hhmm = (iso) => format(new Date(iso), "HH:mm");

// The status line of an endpoint (section 11.2), from the endpoint row and its
// registry entry (health.state, health.reason, health.since).
export function statusLine(endpoint) {
  const host = endpoint?.url_host || "the endpoint";
  const health = endpoint?.health || {};
  if (endpoint?.status === "blocked_by_policy") {
    return {
      tone: "error",
      text: "Blocked: not in this workspace's allowed hosts",
    };
  }
  if (endpoint?.status === "disabled_auto") {
    if (endpoint.disabled_reason === "gone_410") {
      return { tone: "error", text: `Turned off: ${host} answered 410 Gone` };
    }
    if (endpoint.disabled_reason === "unsafe_address") {
      return {
        tone: "error",
        text: `Turned off: ${host} resolves to a private or internal address`,
      };
    }
    const n = health.consecutive_failed_attempts;
    return {
      tone: "error",
      text: `Turned off: every attempt failed for a day${n ? ` (${n} attempts)` : ""}`,
    };
  }
  if (endpoint?.status === "disabled_manual") {
    if (endpoint.disabled_reason === "creator_removed") {
      return {
        tone: "error",
        text: "Turned off: its creator left this workspace",
      };
    }
    return { tone: "default", text: "Turned off" };
  }
  if (health.failing_since) {
    return {
      tone: "warning",
      text: `Failing since ${hhmm(health.failing_since)} · retrying`,
    };
  }
  if (health.lane === "slow") {
    return { tone: "warning", text: "Slow: answers take over 5 s" };
  }
  return { tone: "success", text: "Healthy" };
}

// "214 deliveries waiting · oldest 14 min" (section 11.2), or null when not behind.
export function lagLine(backlog) {
  if (!backlog) return null;
  const waiting = (backlog.due || 0) + (backlog.paused || 0);
  if (!waiting) return null;
  const oldest = [backlog.oldest_due_at, backlog.oldest_paused_at]
    .filter(Boolean)
    .sort()[0];
  const noun = waiting === 1 ? "delivery" : "deliveries";
  return oldest
    ? `${waiting.toLocaleString()} ${noun} waiting · oldest ${formatDistanceToNowStrict(new Date(oldest))}`
    : `${waiting.toLocaleString()} ${noun} waiting`;
}

export const DELIVERY_STATUS = {
  pending: { label: "Pending", tone: "default" },
  sending: { label: "Sending", tone: "info" },
  retrying: { label: "Retrying", tone: "warning" },
  succeeded: { label: "Delivered", tone: "success" },
  failed: { label: "Failed", tone: "error" },
  paused: { label: "Paused", tone: "default" },
  skipped: { label: "Skipped", tone: "default" },
  cancelled: { label: "Cancelled", tone: "default" },
};

export const OUTCOME_TEXT = {
  success: "delivered",
  http_error: "error answer",
  rejected: "rejected",
  timeout: "timed out",
  connect_error: "could not connect",
  dns_error: "name did not resolve",
  tls_error: "TLS failed",
  ssrf_blocked: "blocked address",
  redirect_refused: "redirect refused",
  gone: "410 Gone",
  worker_lost: "worker lost",
  superseded: "superseded",
  not_retried_413: "413, not retried",
};

export const fmtMs = (ms) =>
  ms == null ? "–" : `${Number(ms).toLocaleString()} ms`;

export const fmtTime = (iso) => (iso ? format(new Date(iso), "HH:mm:ss") : "–");
export const fmtDateTime = (iso) =>
  iso ? format(new Date(iso), "d MMM HH:mm") : "–";

export function attemptLine(a, host) {
  const parts = [`Attempt ${a.n}`, fmtTime(a.started_at)];
  if (a.response_status) parts.push(`${a.response_status} from ${host}`);
  else parts.push(OUTCOME_TEXT[a.outcome] || a.outcome);
  const timing = [];
  if (a.dns_ms != null) timing.push(`DNS ${a.dns_ms}`);
  if (a.ttfb_ms != null)
    timing.push(`first byte ${a.ttfb_ms.toLocaleString()}`);
  parts.push(
    `${fmtMs(a.duration_ms)}${timing.length ? ` (${timing.join(" · ")})` : ""}`,
  );
  if (a.resolved_ip) parts.push(a.resolved_ip);
  if (a.trigger && a.trigger !== "auto") parts.push(a.trigger);
  return parts.join(" · ");
}

export function secretEnvLine(secret) {
  return `FI_WEBHOOK_SECRET=${secret}\n`;
}

// The three verify recipes of DESIGN.md section 5.4, verbatim, each with the raw-body note.
export const RAW_BODY_NOTE =
  'Verify the bytes exactly as received: Django request.body, Flask request.get_data(), Express express.raw({type: "application/json"}). A body that is parsed and re-serialized before verification is the most common cause of failures.';

export const VERIFY_SNIPPETS = {
  sdk: `from fi.webhooks import verify, WebhookVerificationError
event = verify(body=request.body, headers=request.headers, secret=os.environ["FI_WEBHOOK_SECRET"])  # dict
if event.get("test"):
    return HttpResponse(status=200)`,
  standard: `from standardwebhooks.webhooks import Webhook
payload = Webhook(os.environ["FI_WEBHOOK_SECRET"]).verify(request.body, request.headers)`,
  stdlib: `import base64, hashlib, hmac, time

def verify_futureagi(secret: str, headers, body: bytes, tolerance_s: int = 300) -> bool:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    msg_id, ts = headers["webhook-id"], headers["webhook-timestamp"]
    if abs(time.time() - int(ts)) > tolerance_s:
        return False
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return any(s.startswith("v1,") and hmac.compare_digest(s[3:], expected)
               for s in headers["webhook-signature"].split(" "))`,
};

// The Activity card's line per audit entry (DESIGN.md section 9.2 actions).
export const ACTIVITY_ACTIONS = {
  "webhook.endpoint.created": "Created",
  "webhook.endpoint.updated": "Settings changed",
  "webhook.endpoint.deleted": "Deleted",
  "webhook.endpoint.enabled": "Turned on",
  "webhook.endpoint.disabled": "Turned off",
  "webhook.endpoint.auto_disabled": "Turned off automatically",
  "webhook.endpoint.blocked_by_policy": "Blocked by the webhook policy",
  "webhook.endpoint.resumed": "Held deliveries resumed",
  "webhook.endpoint.owner_changed": "Owner changed",
  "webhook.endpoint.payload_full_enabled": "Prompt text turned on",
  "webhook.endpoint.run_targeting_enabled": "Run targeting turned on",
  "webhook.secret.created": "Signing secret created",
  "webhook.secret.rotated": "Signing secret rotated",
  "webhook.secret.previous_revoked": "Previous signing secret stopped",
  "webhook.secret.imported": "Signing secret imported",
  "webhook.test.sent": "Test sent",
  "webhook.delivery.redelivered": "Redelivered",
};

const FIELD_WORDS = {
  name: "name",
  description: "description",
  url_display: "URL",
  url_sha256: null,
  subscriptions: "events",
  payload_mode: "payload",
  include_people_names: "people's names",
  accepts_run_targeting: "run targeting",
  header_names: "headers",
  timeout_ms: "timeout",
  rate_limit_per_s: "rate",
  max_in_flight: "in flight",
  max_age_s: "retry window",
  status: "status",
};

export function activityText(entry) {
  const what = ACTIVITY_ACTIONS[entry.action] || entry.action;
  const m = entry.metadata || {};
  const extra = [];
  const fields = (entry.changed || [])
    .map((f) => (f in FIELD_WORDS ? FIELD_WORDS[f] : f))
    .filter(Boolean);
  if (fields.length) extra.push(fields.join(", "));
  // A rule's code (gone_410, creator_removed) reads as words; a reason someone
  // typed is shown as typed.
  if (m.reason)
    extra.push(
      /^[a-z0-9_]+$/.test(m.reason) ? m.reason.replace(/_/g, " ") : m.reason,
    );
  if (entry.action === "webhook.delivery.redelivered")
    extra.push(m.bulk ? `${m.count ?? 0} deliveries` : `attempt ${m.attempt}`);
  if (entry.action === "webhook.test.sent" && m.event_type)
    extra.push(
      m.status_code ? `${m.event_type}, ${m.status_code}` : m.event_type,
    );
  if (entry.action === "webhook.endpoint.resumed" && m.mode)
    extra.push(m.mode === "send" ? "sent" : "skipped");
  return extra.length ? `${what}: ${extra.join(" · ")}` : what;
}
