// What a gateway test request actually did, read from the gateway's own
// answer (Django test-playground returns its status, body, the x-agentcc /
// guardrail headers, and what they say: blocked, warned, guardrail_action
// and triggered_checks).
//
// The gateway has no 246 or 446 status. Only the relay's blocked boolean
// together with HTTP 403 selects a block; upstream body types cannot select
// a rendering path (see guardrailOutcome.js for the published contract). A
// 2xx with X-Agentcc-Guardrail-Triggered: true is a check that warned or
// logged and let the request through: warned, with the action from
// X-Agentcc-Guardrail-Action and the check names from
// X-Agentcc-Guardrails-Triggered when the gateway sends them. Anything else
// that is not 2xx failed for another reason (provider, key, rate limit): an
// error, never a pass and never a block.

import { ruleLabel } from "../catalog/rule-model";
import { providerLabel } from "../../utils/providerLabel";
import { readManagedRefusal } from "../../utils/managedRefusals";
import { readResponseHeader as header } from "../../utils/managedRefusalEvidence";
import { isRelayGuardrailBlock } from "../../utils/guardrailOutcome";

const BLOCK_PREFIX =
  /^Request blocked by guardrail:\s*([^—]+?)\s*—\s*([\s\S]*)$/;

function parseBody(body) {
  if (typeof body !== "string") return body || {};
  try {
    return JSON.parse(body);
  } catch {
    return { text: body };
  }
}

function answerText(body) {
  const content = body?.choices?.[0]?.message?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : part?.text || ""))
      .join("");
  }
  return body?.text || "";
}

const toNumber = (value) => {
  if (value === undefined || value === null || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

function checkNames(headers) {
  const listed = header(headers, "X-Agentcc-Guardrails-Triggered");
  return typeof listed === "string"
    ? listed
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean)
    : [];
}

function passedOutcome(triggered, headers) {
  // N11: a non-blocking outcome requires the gateway's triggered header.
  if (!triggered) return "passed";
  const action = header(headers, "X-Agentcc-Guardrail-Action");
  if (action === "warn") return "warned";
  if (action === "log") return "logged";
  return "triggered";
}

export function interpretTestResult(result) {
  const statusCode = Number(result?.status_code ?? result?.statusCode ?? 0);
  const headers = result?.guardrail_headers ?? result?.guardrailHeaders ?? {};
  const body = parseBody(result?.body);
  const error =
    body?.error && typeof body.error === "object" ? body.error : null;
  const ok = statusCode >= 200 && statusCode < 300;
  const triggered = header(headers, "X-Agentcc-Guardrail-Triggered") === "true";

  const facts = {
    statusCode,
    model: header(headers, "X-Agentcc-Model-Used") || result?.model || null,
    provider:
      providerLabel(
        header(headers, "X-Agentcc-Provider"),
        header(headers, "X-Agentcc-Billing-Mode"),
      ) || null,
    // The request log's id: the generation id where the gateway sends one,
    // else the request id every answer carries.
    requestId:
      header(headers, "X-Agentcc-Generation-Id") ||
      header(headers, "X-Agentcc-Request-Id") ||
      null,
    latencyMs: toNumber(header(headers, "X-Agentcc-Latency-Ms")),
    cost: toNumber(header(headers, "X-Agentcc-Cost")),
    checks: checkNames(headers),
    headers,
  };

  if (isRelayGuardrailBlock(result)) {
    const match = BLOCK_PREFIX.exec(error?.message || "");
    return {
      ...facts,
      outcome: "blocked",
      rule: match ? match[1].trim() : null,
      reason: match ? match[2].trim() : error?.message || "",
      answer: "",
    };
  }
  if (!ok) {
    const refusal = readManagedRefusal(result);
    return {
      ...facts,
      // A refusal reference is the upstream request header, never a generation
      // ID or a body value. Successful guardrail log links remain unchanged.
      requestId: refusal?.evidence.requestId ?? null,
      outcome: "failed",
      refusal,
      rule: null,
      reason:
        error?.message ||
        (typeof error === "string" ? error : "") ||
        (typeof body?.text === "string" ? body.text : ""),
      answer: "",
    };
  }
  return {
    ...facts,
    outcome: passedOutcome(triggered, headers),
    rule: null,
    reason: "",
    answer: answerText(body),
  };
}

const named = (r, did) =>
  r.checks?.length
    ? `A guardrail ${did}: ${r.checks.join(", ")}.`
    : `A guardrail ${did}. This gateway does not name the checks.`;

// Each outcome's chip (label with the status code), its short name in the
// history, and the sentence the result states. A provider error is an error,
// never green.
export const OUTCOMES = {
  blocked: {
    tone: "error",
    label: "Blocked",
    short: () => "Blocked",
    sentence: (r) =>
      r.rule
        ? `${ruleLabel(r.rule)} refused the prompt or the model's answer with a 403.`
        : "Request was blocked by guardrails: a rule configured to block refused the prompt or the model's answer.",
  },
  warned: {
    tone: "warning",
    label: "Warned",
    short: () => "Warned",
    sentence: (r) => named(r, "warned and let the request through"),
  },
  logged: {
    tone: "neutral",
    label: "Logged",
    short: () => "Logged",
    sentence: (r) => named(r, "logged the request and let it through"),
  },
  triggered: {
    tone: "warning",
    label: "Triggered",
    short: () => "Triggered",
    sentence: (r) => named(r, "triggered and let the request through"),
  },
  passed: {
    tone: "healthy",
    label: "Passed",
    short: () => "Passed",
    sentence: (r) =>
      `No guardrail triggered. The request completed (HTTP ${r.statusCode}).`,
  },
  failed: {
    tone: "error",
    label: "Error",
    short: (r) => `Error ${r.statusCode}`,
    sentence: (r) =>
      `The request failed with HTTP ${r.statusCode || "an error"}${r.reason ? `: ${r.reason}` : ""}. No guardrail blocked it.`,
  },
};

/** The outcome chip's label: what happened and the HTTP status. */
export const outcomeLabel = (r) =>
  `${OUTCOMES[r.outcome].label} (${r.statusCode})`;
