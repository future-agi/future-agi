import { paths } from "src/routes/paths";
import { CREDITS_ANCHOR } from "src/sections/settings/BillingV2/gateway-credits/links";

import { isRelayGuardrailBlock } from "./guardrailOutcome";
import { readAdditionalRefusal } from "./managedRefusalRows";
import { readRetryAfterSeconds } from "./managedRefusalTiming";
import {
  readResponseEvidence,
  readResponseHeader,
} from "./managedRefusalEvidence";
import {
  readAmbiguousModelRefusal,
  readBudgetStateRefusal,
  readSpendRefusal,
} from "./managedRefusalVariants";

const text = (value) => (typeof value === "string" ? value.trim() : "");
const TOP_UP = {
  label: "Top up credits",
  href: `${paths.dashboard.settings.billing}#${CREDITS_ANCHOR}`,
};
const BUDGETS = {
  label: "Open gateway budgets",
  href: paths.dashboard.gateway.budgets,
};
const NO_RETRY = "The console will not retry automatically.";

/**
 * Classify the HTTP status and error.code, never the ambiguous error.type.
 * Remedy metadata is allowlisted by readManagedRefusal. retryAfter must be
 * response-header-derived; never pass body timing or scalar failure aliases.
 * An action without href is guidance, not a replay button or a mutation.
 */
export function classify(httpStatus, errorCode, metadata = {}) {
  const detail = metadata || {};
  const message = text(detail.message);
  // Known variants use controlled copy, not amounts, run IDs or other values
  // embedded in error.message. Unknown variants also use value-free copy.
  const mapped = (category, title, guidance, action) => ({
    category,
    title,
    body: guidance,
    action,
  });

  if (typeof errorCode !== "string" || !errorCode.trim()) {
    return mapped(
      "other",
      "Request failed",
      `The gateway refused the request. ${NO_RETRY}`,
      null,
    );
  }
  switch (`${httpStatus}:${errorCode}`) {
    case "402:managed_credits_exhausted":
      return mapped(
        "out_of_credits",
        "Future AGI credits are used up",
        "Open Billing to add credits. If top-ups are unavailable, ask your organization's billing administrator for credit or use your own provider key.",
        TOP_UP,
      );
    case "402:managed_credits_insufficient":
      if (
        detail.limit_source === "agentcc_in_flight" &&
        detail.remedy_hint === "retry"
      ) {
        return mapped(
          "out_of_credits",
          "Your balance is in use",
          `Other requests are using your balance. Wait for them to finish, then try again. ${NO_RETRY}`,
          { label: "Try again after those requests finish" },
        );
      }
      return mapped(
        "out_of_credits",
        "Not enough Future AGI credits",
        "Add credits, lower max tokens, or use your own provider key. If top-ups are unavailable, ask your organization's billing administrator for credit.",
        TOP_UP,
      );
    case "402:agent_budget_exhausted":
      return mapped(
        "agent_budget",
        "Agent budget exhausted",
        "Review the agent run budget. Raise it or start a new run with an appropriate budget; lowering max tokens or choosing a cheaper model may also help. Adding credits does not change this budget.",
        { label: "Review the agent run budget" },
      );
    case "429:budget_exceeded":
    case "429:org_budget_exceeded":
      return mapped(
        "budget_limit",
        errorCode === "org_budget_exceeded"
          ? "Organization budget exceeded"
          : "Gateway budget exceeded",
        "Review the applicable budget in Gateway > Budgets before sending another request.",
        BUDGETS,
      );
    case "429:managed_spend_velocity":
      return readSpendRefusal(detail);
    case "503:budget_state_unavailable":
      return readBudgetStateRefusal(detail);
    // Forward-compatible r4 tuple; the r3 producer is not attested by this UI.
    case "400:managed_model_ambiguous":
      return readAmbiguousModelRefusal(detail);
    case "429:rate_limit_exceeded": {
      const retryAfter = readRetryAfterSeconds(detail.retryAfter);
      return mapped(
        "rate_limit",
        "Rate limit reached",
        `${retryAfter !== null ? `Retry-After: ${retryAfter}. ` : ""}Wait before trying again. ${NO_RETRY}`,
        { label: "Try again after waiting" },
      );
    }
    case "503:managed_billing_unavailable":
      return mapped(
        "billing_unavailable",
        "Billing is temporarily unavailable",
        `Try again later or use your own provider key. ${NO_RETRY}`,
        { label: "Try again later" },
      );
    case "403:future_agi_credits_off":
      return mapped(
        "permission",
        "Future AGI credits are off",
        "Future AGI credits are off for your organization, so the gateway refused this request. Use your own provider key, or ask an Owner or Admin to contact your Future AGI account team to turn them on.",
        // The console only turns credits off; no page turns them on. Name the
        // real path, with no link to a control that does not exist.
        { label: "Ask an Owner or Admin to contact your Future AGI account team" },
      );
    case "401:invalid_api_key":
      return mapped(
        "invalid_key",
        "Gateway API key is invalid",
        "Create or rotate a gateway API key in Gateway > API keys and send it in the supported authentication header. Build Keys and provider-vendor keys are not gateway credentials.",
        { label: "Open gateway API keys", href: paths.dashboard.gateway.keys },
      );
    // Forward-compatible typed admission code requested for this consumer.
    // model/reason metadata may be present but never enters the alert copy.
    case "403:managed_model_unpriced":
      return mapped(
        "unpriced_model",
        "Model pricing is unavailable",
        "This model cannot use Future AGI credits without supported pricing. Choose a priced model or use your own provider key.",
        { label: "Choose a priced model or use your own provider key" },
      );
    case "403:permission_denied":
      // Legacy explanatory copy stays available, but prose must never change
      // the permission category. Only the distinct typed code is unpriced_model.
      if (
        /\bmodel pricing is not available for Future AGI credits\b/i.test(
          message,
        )
      ) {
        return mapped(
          "permission",
          "Model pricing is unavailable",
          "This model cannot use Future AGI credits without supported pricing. Choose a priced model or use your own provider key.",
          { label: "Choose a priced model or use your own provider key" },
        );
      }
      return mapped(
        "permission",
        "Permission denied",
        "An access policy does not allow this request. Ask an authorized administrator to review model, provider, key or IP access.",
        { label: "Ask an administrator to review access" },
      );
    default:
      break;
  }
  const additional = readAdditionalRefusal(httpStatus, errorCode, detail);
  if (additional) return additional;
  return {
    category: "other",
    title: "Request failed",
    // Relayed prose can spoof timing and billing claims, even for unknown codes.
    body: `The gateway refused the request. ${NO_RETRY}`,
    action: null,
  };
}

function parseBody(body) {
  if (typeof body !== "string") return body || {};
  try {
    return JSON.parse(body);
  } catch {
    return { detail: body };
  }
}

/** Read the playground JSON result or SSE result event, without changing it. */
export function readManagedRefusal(result) {
  const status = Number(result?.status_code ?? result?.statusCode);
  const body = parseBody(result?.body);
  const error = body?.error;
  if (!(status >= 400 && status < 600) || isRelayGuardrailBlock(result)) {
    return null;
  }
  const message =
    text(error?.message) ||
    text(error) ||
    text(body?.detail) ||
    text(body?.text);
  const headers = result?.guardrail_headers ?? result?.headers;
  return {
    ...classify(status, error?.code, {
      // Allowlist remedy inputs. Dispatch/request identity never come from the
      // body, including error.metadata, or locally generated IDs.
      reason: error?.metadata?.reason,
      remedy_hint: error?.metadata?.remedy_hint,
      limit_source: error?.metadata?.limit_source,
      candidates: error?.metadata?.candidates,
      param: error?.param,
      message,
      retryAfter: readResponseHeader(headers, "retry-after"),
    }),
    evidence: readResponseEvidence(headers),
  };
}

/** Direct HTTP failures: the app's Axios error shape, or unwrapped Axios. */
export function readManagedFailure(failure) {
  const response = failure?.response;
  const body = response?.data || failure;
  // Recognized console envelopes are not gateway refusal evidence. Keep the
  // existing safe application/operator messages, without granting any replay.
  if (
    body?.code === "gateway_request_rejected" ||
    ((response?.status ?? failure?.statusCode) === 400 &&
      body?.status === false &&
      typeof body?.result === "string" &&
      !body?.error &&
      body?.code == null)
  )
    return null;
  return (
    readManagedRefusal(body?.result) ||
    readManagedRefusal({
      status_code: response?.status ?? failure?.statusCode,
      body,
      guardrail_headers: response?.headers ?? failure?.responseHeaders,
    })
  );
}
