// The Add webhook dialog's state and its wire body (DESIGN.md sections 10 and
// 11.2). The form's shape is not the wire's: subscriptions carry their own
// filters, delivery settings go out in ms and seconds.
import {
  ENVIRONMENTS,
  FAILURE_STATUSES,
  LABEL_ACTIONS,
  TRIGGER_KINDS,
} from "./copy";

export const ALL_EVENTS = [
  "prompt.version.created",
  "prompt.label.changed",
  "deployment.run.completed",
  "deployment.run.failed",
];

export function emptyForm({ orgWide = false } = {}) {
  return {
    name: "",
    url: "",
    orgWide,
    events: Object.fromEntries(ALL_EVENTS.map((t) => [t, false])),
    promptMode: "all",
    prompts: [], // [{id, name}]
    labelMode: "any",
    labelNames: [],
    actions: LABEL_ACTIONS.map((a) => a.value),
    environments: [],
    triggerKinds: [],
    statuses: FAILURE_STATUSES.filter((s) => s.byDefault).map((s) => s.value),
    payloadMode: "thin",
    includePeopleNames: true,
    headers: [],
    timeoutS: 15,
    ratePerS: 10,
    maxInFlight: 4,
    maxAgeH: 72,
    acceptsRunTargeting: false,
    // Subscriptions the form cannot show as checkboxes and "Only for" filters
    // (made with the API or the CLI): kept exactly as they are, shown read-only.
    keptSubs: [],
  };
}

const some = (list) => (list && list.length ? list : undefined);

export function subscriptionsOf(form) {
  const promptIds =
    form.promptMode === "these"
      ? some(form.prompts.map((p) => p.id))
      : undefined;
  const subs = [];
  for (const type of ALL_EVENTS) {
    if (!form.events[type]) continue;
    const filters = {};
    if (type.startsWith("prompt.") && promptIds) filters.prompt_ids = promptIds;
    if (type === "prompt.label.changed") {
      if (form.labelMode === "these" && some(form.labelNames)) {
        filters.label_names = form.labelNames;
      }
      if (form.actions.length && form.actions.length < LABEL_ACTIONS.length) {
        filters.actions = form.actions;
      }
    }
    if (type.startsWith("deployment.run.")) {
      if (some(form.environments)) filters.environments = form.environments;
      if (some(form.triggerKinds)) filters.trigger_kinds = form.triggerKinds;
    }
    if (type === "deployment.run.failed" && some(form.statuses)) {
      filters.statuses = form.statuses;
    }
    subs.push({ event_type: type, filters });
  }
  return subs;
}

export function headersOf(form) {
  const rows = form.headers.filter((h) => h.name.trim());
  return Object.fromEntries(rows.map((h) => [h.name.trim(), h.value]));
}

export function createBody(form, { workspaceId }) {
  const body = {
    name: form.name.trim(),
    url: form.url.trim(),
    subscriptions: subscriptionsOf(form),
    payload_mode: form.payloadMode,
    include_people_names: form.includePeopleNames,
    timeout_ms: Math.round(Number(form.timeoutS) * 1000),
    rate_limit_per_s: Number(form.ratePerS),
    max_in_flight: Number(form.maxInFlight),
    max_age_s: Math.round(Number(form.maxAgeH) * 3600),
  };
  if (form.orgWide) body.workspace_id = null;
  else if (workspaceId) body.workspace_id = workspaceId;
  const headers = headersOf(form);
  if (Object.keys(headers).length) body.headers = headers;
  if (form.acceptsRunTargeting) body.accepts_run_targeting = true;
  return body;
}

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The endpoint page's Settings card edits the same fields. It sends only what
// the person changed, measured against the form the saved endpoint opens as:
// - PATCH replaces every subscription when `subscriptions` is sent, so they are
//   sent only when the checkboxes or filters changed, and then with the kept
//   ones (wildcards, deployment and excluded-prompt filters) exactly as saved;
// - the admin-only fields (run targeting, the full payload) go out only when
//   changed, so a Member who may manage the webhook can still save the rest;
// - header values are write-only (the API never returns them), so headers are
//   sent only when the person changed them.
export function updateBody(form, original) {
  const before = createBody(formOf(original), {});
  const after = createBody(form, {});
  const body = {};
  for (const key of [
    "name",
    "payload_mode",
    "include_people_names",
    "timeout_ms",
    "rate_limit_per_s",
    "max_in_flight",
    "max_age_s",
  ]) {
    if (!sameJson(after[key], before[key])) body[key] = after[key];
  }
  const url = form.url.trim();
  if (form.urlMasked ? url !== "" : url !== (original?.url || ""))
    body.url = url;
  if (!sameJson(subscriptionsOf(form), subscriptionsOf(formOf(original))))
    body.subscriptions = [...subscriptionsOf(form), ...form.keptSubs];
  if (!!form.acceptsRunTargeting !== !!original?.accepts_run_targeting) {
    body.accepts_run_targeting = !!form.acceptsRunTargeting;
  }
  if (form.headersTouched) body.headers = headersOf(form);
  return body;
}

// Saving headers replaces them all (PATCH /webhooks/endpoints/{id}/ stores the
// map it is sent), and a saved header's value is never sent back. So once the
// headers are changed, every saved header that is kept needs its value typed
// again, or saving would blank it. Returns the names still waiting for a value.
export function headersWithoutValue(form) {
  if (!form.headersTouched) return [];
  return form.headers
    .filter((h) => h.saved && h.name.trim() && h.value === "")
    .map((h) => h.name.trim());
}

export function headersRetypeMessage(names) {
  const list = names.join(", ");
  return names.length === 1
    ? `Type the value of ${list} again. Values are hidden after saving, and saving the headers replaces them all.`
    : `Type the values of ${list} again. Values are hidden after saving, and saving the headers replaces them all.`;
}

// The filters each event's checkbox and "Only for" fields can show, and the
// values the checkboxes offer (free text otherwise).
const FORM_FILTERS = {
  "prompt.version.created": ["prompt_ids"],
  "prompt.label.changed": ["prompt_ids", "label_names", "actions"],
  "deployment.run.completed": ["environments", "trigger_kinds"],
  "deployment.run.failed": ["environments", "trigger_kinds", "statuses"],
};
const CHOICES = {
  actions: LABEL_ACTIONS.map((o) => o.value),
  environments: ENVIRONMENTS.map((o) => o.value),
  trigger_kinds: TRIGGER_KINDS.map((o) => o.value),
  statuses: FAILURE_STATUSES.map((o) => o.value),
};
// "Only for" filters one field shows for two events: both must agree.
const SHARED = [
  ["prompt.version.created", "prompt.label.changed", ["prompt_ids"]],
  [
    "deployment.run.completed",
    "deployment.run.failed",
    ["environments", "trigger_kinds"],
  ],
];

const filled = (filters) =>
  Object.entries(filters || {}).filter(([, v]) => v != null && v.length);

const fitsForm = (sub) => {
  const keys = FORM_FILTERS[sub.event_type];
  return (
    !!keys &&
    filled(sub.filters).every(
      ([k, v]) =>
        keys.includes(k) &&
        (!CHOICES[k] || v.every((x) => CHOICES[k].includes(x))),
    )
  );
};

// Which subscriptions the form can edit, and which it keeps as they are.
export function splitSubscriptions(subs = []) {
  const kept = new Set(subs.filter((s) => !fitsForm(s)));
  const byType = Object.fromEntries(
    subs.filter((s) => !kept.has(s)).map((s) => [s.event_type, s]),
  );
  for (const [a, b, keys] of SHARED) {
    if (!byType[a] || !byType[b]) continue;
    const norm = (sub, k) => [...(sub.filters?.[k] || [])].sort();
    if (keys.some((k) => !sameJson(norm(byType[a], k), norm(byType[b], k)))) {
      kept.add(byType[a]);
      kept.add(byType[b]);
    }
  }
  return {
    editable: subs.filter((s) => !kept.has(s)),
    kept: subs.filter((s) => kept.has(s)),
  };
}

const FILTER_WORDS = {
  prompt_ids: "prompts",
  exclude_prompt_ids: "except prompts",
  label_names: "labels",
  actions: "changes",
  deployment_ids: "deployments",
  environments: "environments",
  trigger_kinds: "started by",
  statuses: "statuses",
};

// "deployment.run.completed · deployments: dep-A" for the read-only list.
export function keptLine(sub) {
  const parts = filled(sub.filters).map(
    ([k, v]) => `${FILTER_WORDS[k] || k}: ${v.join(", ")}`,
  );
  return [sub.event_type, ...parts].join(" · ");
}

export function formOf(endpoint) {
  const form = emptyForm({ orgWide: endpoint.org_wide });
  form.name = endpoint.name;
  // The API masks every query value of a saved URL (url_display: "?token=…").
  // That text cannot be sent back (the URL rule is ASCII only), so the field
  // starts empty and the URL goes out only once the person types it in full,
  // as with header values (WH-3 review S11).
  form.savedUrl = endpoint.url || "";
  form.savedUrlScheme = endpoint.url_scheme;
  form.urlMasked = form.savedUrl.includes("…");
  form.url = form.urlMasked ? "" : form.savedUrl;
  const { editable, kept } = splitSubscriptions(endpoint.subscriptions || []);
  form.keptSubs = kept.map((s) => ({
    event_type: s.event_type,
    filters: s.filters || {},
  }));
  for (const sub of editable) {
    form.events[sub.event_type] = true;
    const f = sub.filters || {};
    if (f.prompt_ids?.length) {
      form.promptMode = "these";
      form.prompts = f.prompt_ids.map((id) => ({ id, name: id }));
    }
    if (f.label_names?.length) {
      form.labelMode = "these";
      form.labelNames = f.label_names;
    }
    if (f.actions?.length) form.actions = f.actions;
    if (f.environments?.length) form.environments = f.environments;
    if (f.trigger_kinds?.length) form.triggerKinds = f.trigger_kinds;
    if (f.statuses?.length) form.statuses = f.statuses;
  }
  form.payloadMode = endpoint.payload_mode;
  form.includePeopleNames = endpoint.include_people_names;
  form.headers = (endpoint.header_names || []).map((name) => ({
    name,
    value: "",
    saved: true,
  }));
  form.timeoutS = endpoint.timeout_ms / 1000;
  form.ratePerS = endpoint.rate_limit_per_s;
  form.maxInFlight = endpoint.max_in_flight;
  form.maxAgeH = endpoint.max_age_s / 3600;
  form.acceptsRunTargeting = endpoint.accepts_run_targeting;
  return form;
}

// The API's error envelope: `{status: false, code, attr, result|message}`. FA's
// axios interceptor (src/utils/axios.js) rejects with that body spread into a
// plain object plus `statusCode`; a raw Axios error keeps it under `response`.
export function errorBody(error) {
  if (!error) return {};
  return error.response?.data ?? error;
}

export function errorStatus(error) {
  return error?.statusCode ?? error?.response?.status ?? null;
}

export function apiError(error, fallback = "Something went wrong.") {
  const data = errorBody(error);
  const msg =
    (typeof data.result === "string" && data.result) ||
    (typeof data.message === "string" && data.message) ||
    (typeof data.error === "string" && data.error) ||
    data.error?.message ||
    data.detail ||
    fallback;
  return {
    code: data.code || data.error?.code || null,
    attr: data.attr || null,
    message: msg,
  };
}
