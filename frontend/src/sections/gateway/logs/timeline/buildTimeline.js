// What happened to one request, in order, on one time axis: received, the
// guardrail checks that acted on it, a cache hit or each upstream attempt
// (placed at its own start offset with its own latency), then the response.
// Built only from what the log recorded: an attempt whose time the gateway
// did not record is a point, said so, never a guessed bar.

import {
  ATTEMPT_KINDS,
  ATTEMPT_REASONS,
  ERROR_CLASSES,
  labelOf,
} from "../explorerLabels";
import { providerLabel } from "../../utils/providerLabel";

const ms = (value) => {
  const n = Number(value);
  return value != null && value !== "" && Number.isFinite(n) ? n : null;
};

const time = (iso) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : null;
};

function readGuardrails(raw) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object" && Array.isArray(value.checks)) {
    return value.checks;
  }
  return [];
}

const ACTION_WORDS = {
  block: "blocked",
  warn: "warned",
  redact: "redacted",
  mask: "masked",
  log: "logged",
};

const toneOfStatus = (code) => {
  if (code == null) return "neutral";
  if (code >= 500) return "error";
  if (code >= 400) return "warning";
  return "healthy";
};

function attemptTone(attempt) {
  if (attempt.outcome === "success") return "healthy";
  if (attempt.outcome === "skipped" || attempt.outcome === "discarded") {
    return "neutral";
  }
  const code = ms(attempt.status_code);
  if (code != null && code >= 400 && code < 500) return "warning";
  return "error";
}

function attemptDetail(attempt) {
  const parts = [];
  const code = ms(attempt.status_code);
  if (code) parts.push(String(code));
  if (attempt.outcome === "skipped") parts.push("not sent");
  if (attempt.error_class)
    parts.push(labelOf(ERROR_CLASSES, attempt.error_class));
  if (attempt.reason)
    parts.push(`after ${labelOf(ATTEMPT_REASONS, attempt.reason)}`);
  if (ms(attempt.latency_ms) == null && attempt.outcome !== "skipped") {
    parts.push("time not recorded");
  }
  return parts.join(" · ");
}

export function buildTimeline(log) {
  const start = time(log?.started_at);
  if (start == null) return { events: [], totalMs: 0 };

  const latency = ms(log.latency_ms) ?? 0;
  const status = ms(log.status_code);
  const events = [
    {
      kind: "received",
      label: "Received",
      offsetMs: 0,
      durationMs: null,
      tone: "neutral",
    },
  ];

  readGuardrails(log.guardrail_results)
    .filter((check) => check && check.action && check.action !== "pass")
    .forEach((check) => {
      events.push({
        kind: "guardrail",
        label: `Guardrail ${check.name || "check"}: ${ACTION_WORDS[check.action] || check.action}`,
        detail: check.message || check.details || "",
        offsetMs: 0,
        durationMs: null,
        tone: check.action === "block" ? "error" : "warning",
      });
    });

  const attempts = (Array.isArray(log.attempts) ? log.attempts : [])
    .slice()
    .sort((a, b) => (ms(a.index) ?? 0) - (ms(b.index) ?? 0));

  let end = latency;
  attempts.forEach((attempt) => {
    const at = time(attempt.started_at);
    const offset = at == null ? null : Math.max(0, Math.round(at - start));
    const duration = ms(attempt.latency_ms);
    if (offset != null && duration != null)
      end = Math.max(end, offset + duration);
    // A managed attempt names Future AGI, not its upstream;
    // Attempt details keep the upstream.
    const provider = attempt.provider
      ? providerLabel(attempt.provider, attempt.credential_type)
      : "";
    const target = [provider, attempt.model].filter(Boolean).join(" / ");
    events.push({
      kind: "attempt",
      index: ms(attempt.index),
      label: `${labelOf(ATTEMPT_KINDS, attempt.kind || "primary")}${target ? ` · ${target}` : ""}`,
      detail: attemptDetail(attempt),
      offsetMs: offset,
      durationMs: duration,
      tone: attemptTone(attempt),
      billable: Boolean(attempt.billable),
    });
  });

  const blocked = events.some(
    (e) => e.kind === "guardrail" && e.tone === "error",
  );
  if (attempts.length === 0 && log.cache_hit) {
    events.push({
      kind: "cache",
      label: "Served from the gateway cache",
      detail: "No upstream call was made",
      offsetMs: 0,
      durationMs: latency || null,
      tone: "healthy",
    });
  } else if (
    attempts.length === 0 &&
    !blocked &&
    status != null &&
    status >= 400
  ) {
    events.push({
      kind: "refused",
      label: "Refused by the gateway",
      detail: log.error_message || "",
      offsetMs: 0,
      durationMs: null,
      tone: toneOfStatus(status),
    });
  }

  const noUpstream =
    attempts.length === 0 &&
    !log.cache_hit &&
    (blocked || (status != null && status >= 400));
  events.push({
    kind: "response",
    label: status != null ? `Responded ${status}` : "Responded",
    detail: noUpstream ? "No upstream call was made" : "",
    offsetMs: latency,
    durationMs: null,
    tone: blocked ? "error" : toneOfStatus(status),
  });

  return { events, totalMs: end };
}
