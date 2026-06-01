/**
 * Task error classifier.
 *
 * Users complained that task error logs weren't actionable — raw strings like
 * "Error during evaluation: Required attribute 'output' for key 'result' not
 * found for span <uuid>" give them no idea what to fix. This module maps the
 * known error strings the backend produces (see `tracer/utils/eval.py` and
 * `tracer/utils/external_eval.py`) into structured categories with a title,
 * a severity, a normalized message, and actionable hints.
 *
 * The backend (`tracer/views/eval_task.py::get_eval_task_logs`) now pre-
 * aggregates errors by a normalized form in SQL — it returns an
 * `error_groups` array of `{ normalized, count, sample }` rows. This
 * module no longer does client-side grouping; `classifyTaskError` is
 * called per group to attach the title / icon / severity / hints based
 * on the sample. Keeping the classifier client-side lets us iterate on
 * copy and hints without a backend deploy.
 */

// Strip the uniform "Error during evaluation: " prefix the backend wraps
// every error with (see eval.py:250, 254, 438, 442, 585, 590, 796, 800).
const stripPrefix = (msg) =>
  (msg || "").replace(/^Error during evaluation:\s*/i, "").trim();

// Category definitions. Order matters — more-specific patterns first.
// Each category has:
//   match: regex against the prefix-stripped message
//   title: short human-readable label ("Missing attribute on spans")
//   icon: solar iconify icon (theme-reactive via MUI sx)
//   severity: always "error" — every entry in this log is a failed eval
//     run that produced no result. The "warning" tier was removed
//     because it visually downgraded real failures (the amber color
//     made users assume they were soft alerts). Whether the failure
//     is transient or permanent is communicated in the hint copy
//     ("almost always transient — re-run the task") rather than via
//     a softer color.
//   hints(match): list of actionable next-step strings, may interpolate
//     regex capture groups
//   normalize(stripped, match): canonical form used for grouping — strips
//     span IDs, request IDs, and other per-row noise so duplicate errors
//     collapse into one group
const CATEGORIES = [
  {
    id: "missing_attribute",
    // The backend produces two slightly different shapes for the same
    // root cause (an eval template variable wasn't resolvable on the
    // matching span):
    //   eval.py:82
    //     "Required attribute 'X' for key 'Y' not found for span <id>"
    //   newer codepaths
    //     "Required key X not found for span <id>"
    // We match both with one regex. The capture groups land in
    // different positions per branch, so the hint builder normalizes:
    //   m[1] / m[2] = (attribute, key) for the older form
    //   m[3]        = key for the newer form
    match:
      /Required (?:attribute '([^']+)' for key '([^']+)'|key (\S+))\s+not found/i,
    title: "Missing attribute on spans",
    icon: "solar:link-broken-linear",
    severity: "error",
    hints: (m) => {
      const attribute = m[1];
      const key = m[2] || m[3];
      if (attribute) {
        return [
          `The eval template expects the variable "${key}" to be mapped to a field named "${attribute}", but that field wasn't found on the matching spans.`,
          "Open the Details tab and check the Variable Mapping for this eval — the mapped field may have been renamed or isn't present on every span.",
          "Use the Test button to preview a sample span and confirm which fields exist.",
          `If your SDK is supposed to send "${attribute}", verify it's being attached to every span, not just some.`,
        ];
      }
      return [
        `The eval template expects a "${key}" value on each span, but it wasn't found.`,
        `Open the Details tab and check the Variable Mapping for this eval — make sure "${key}" is mapped to a field that exists on the matching spans.`,
        "Use the Test button to preview a sample span and confirm which fields are available.",
        `If your SDK is supposed to attach "${key}" to spans, verify it's being sent on every span, not just some.`,
      ];
    },
    // Normalize: strip the trailing "for span <id>" so all variants of
    // the same error collapse into one group. The id can be a UUID
    // (hyphenated) or a bare hex string — both match `[a-f0-9-]+`.
    normalize: (stripped) => stripped.replace(/ for span [a-f0-9-]+$/i, ""),
  },
  {
    id: "quota_exceeded",
    // external_eval.py:58, eval.py:135 — "Usage limit exceeded" (or a
    // custom reason from check_usage)
    match: /Usage limit exceeded|quota.{0,20}exceed|free[\s-]?tier/i,
    title: "Usage limit exceeded",
    icon: "solar:wallet-money-linear",
    severity: "error",
    hints: () => [
      "Your organization has hit its eval quota for the current billing period.",
      "Upgrade your plan from Settings → Billing to raise the limit, or wait for the quota to reset.",
      "To reduce usage without upgrading, lower the sampling rate on this task so fewer spans are evaluated.",
    ],
    normalize: () => "Usage limit exceeded",
  },
  {
    id: "api_not_allowed",
    // eval.py:146, 149, 348, 350 — "API call not allowed : ..."
    match: /API call not allowed/i,
    title: "API call not authorized",
    icon: "solar:shield-cross-linear",
    severity: "error",
    hints: () => [
      "The evaluator couldn't be authorized to make the required API call.",
      "Check that your workspace has valid credentials for the model provider in Settings → Model Providers.",
      "If you recently rotated API keys, re-save them in provider settings so the evaluator picks up the fresh credentials.",
    ],
    normalize: () => "API call not allowed",
  },
  {
    id: "span_not_found",
    // eval.py:299, observation_span.py:1308, 1427, 3075
    match: /Observation span not found/i,
    title: "Span no longer exists",
    icon: "solar:ghost-linear",
    severity: "error",
    hints: () => [
      "The span this eval was scheduled against was deleted before execution finished.",
      "This usually happens when spans are cleaned up mid-task or the project was pruned.",
      "Re-run the task to pick up the current set of spans.",
    ],
    normalize: () => "Observation span not found",
  },
  {
    id: "config_not_found",
    // eval.py:301, observation_span.py:1317, 1436
    match: /Custom eval config not found/i,
    title: "Eval configuration was deleted",
    icon: "solar:file-corrupted-linear",
    severity: "error",
    hints: () => [
      "The eval template or custom config this task uses has been deleted.",
      "Open the Details tab, remove the missing eval, add a replacement, and save the task to continue.",
    ],
    normalize: () => "Custom eval config not found",
  },
  {
    id: "rate_limit",
    match: /rate[\s-]?limit|\b429\b|too many requests/i,
    title: "Rate-limited by model provider",
    icon: "solar:speedometer-max-linear",
    severity: "error",
    hints: () => [
      "The evaluator's LLM call was rate-limited by the model provider.",
      "This is almost always transient — rerun the task in a few minutes.",
      "If it's happening consistently, check your provider's rate-limit quotas, or switch this eval to a model with higher throughput.",
    ],
    // Keep the matched message as-is (minus any trailing identifiers) so
    // users can see which provider rate-limited them.
    normalize: (stripped) => stripped.replace(/ req.{0,20}[a-f0-9-]{8,}$/i, ""),
  },
  {
    id: "llm_auth",
    match: /\b(401|authentication|unauthorized)\b|invalid.{0,10}api.{0,10}key/i,
    title: "Model provider authentication failed",
    icon: "solar:key-square-linear",
    severity: "error",
    hints: () => [
      "The evaluator couldn't authenticate with the model provider.",
      "Check that the API key in Settings → Model Providers is still valid.",
      "If the key was rotated recently, update it in the workspace config and retry the task.",
    ],
    normalize: (stripped) => stripped.slice(0, 160),
  },
  {
    id: "llm_5xx",
    match:
      /\b(500|502|503|504)\b|service unavailable|internal server error|bad gateway/i,
    title: "Model provider is unavailable",
    icon: "solar:server-square-linear",
    severity: "error",
    hints: () => [
      "The model provider returned a server-side error.",
      "This is almost always transient — retry the task in a few minutes.",
      "Check the provider's status page if it persists for more than 15 minutes.",
    ],
    normalize: (stripped) => stripped.slice(0, 160),
  },
  {
    id: "llm_timeout",
    match: /\btimeout\b|timed out|ETIMEDOUT|ECONNRESET|ECONNABORTED/i,
    title: "Model provider timeout",
    icon: "solar:clock-circle-linear",
    severity: "error",
    hints: () => [
      "The evaluator's LLM call timed out before a response arrived.",
      "This is usually transient — rerun the task.",
      "For very long inputs, consider switching to a faster model or trimming the mapped variables so fewer tokens are sent.",
    ],
    normalize: (stripped) => stripped.slice(0, 160),
  },
  {
    id: "parse_failure",
    match:
      /JSONDecodeError|could not parse|invalid json|failed.{0,10}parse|unexpected.{0,10}token|malformed/i,
    title: "Couldn't parse model response",
    icon: "solar:document-broken-linear",
    severity: "error",
    hints: () => [
      "The LLM returned a response that couldn't be parsed into the expected output format.",
      "Try a stronger model — smaller models sometimes struggle with structured output.",
      "If the eval uses choice-based output, make sure the choice labels are short and unambiguous so the model returns them verbatim.",
    ],
    normalize: (stripped) => stripped.slice(0, 160),
  },
  {
    id: "image_processing_failure",
    // Multimodal / vision evals that need to load an image from a span
    // attribute. The most common emission is PIL's "cannot identify
    // image file" when the bytes aren't a recognized format. The S3
    // re-upload wrapper pre-pends "Failed to upload image to S3:".
    match:
      /cannot identify image file|Failed to upload image|UnidentifiedImageError|PIL\.UnidentifiedImageError|invalid image/i,
    title: "Image processing failed",
    icon: "solar:gallery-remove-linear",
    severity: "error",
    hints: () => [
      "The eval tried to load an image from a mapped span field but couldn't recognize the file format.",
      "Most common cause: the mapped field is a URL that returned an HTML error page (404/403) instead of actual image bytes — open the Test button and inspect what the field actually contains.",
      "Check that the mapped field is the image URL itself, not a text description of the image.",
      "If the image is HEIC/AVIF/RAW, those formats need extra codecs the evaluator doesn't have — convert to PNG/JPEG before sending.",
    ],
    // BytesIO repr includes a memory address that's per-row noise.
    // Strip it so groups collapse: "<_io.BytesIO object at 0xABC>" → "".
    normalize: (stripped) =>
      stripped.replace(/<_io\.BytesIO object at 0x[0-9a-f]+>/i, "<bytes>"),
  },
  {
    id: "download_failure",
    // Eval tried to fetch a referenced file (PDF, image, audio) and
    // the download failed after retries.
    match:
      /ERROR_DOWNLOADING_DOCUMENT|Max retries exceeded.{0,80}download|Failed to download|Failed to fetch.{0,30}(document|file|url|image)/i,
    title: "Couldn't download referenced file",
    icon: "solar:cloud-cross-linear",
    severity: "error",
    hints: () => [
      "The eval tried to download a file referenced on the span (image, PDF, document) but the URL didn't respond after several retries.",
      "Check that the URL is publicly accessible — presigned S3 URLs expire, and links behind auth (Slack, Notion, Drive, private buckets) won't work.",
      "If the URL is correct, the host may be rate-limiting or temporarily down. Re-run the task later.",
      "For files in your own S3 bucket, make sure the bucket policy allows fetches from the evaluator's IP range.",
    ],
    normalize: (stripped) => stripped.slice(0, 200),
  },
  {
    id: "service_unavailable",
    // Internal serving service unreachable. Catches:
    //   "Failed to connect to serving service: ..."
    //   "Connection aborted"
    //   "RemoteDisconnected"
    //   "Remote end closed connection without response"
    // These are infrastructure-side and transient — almost always
    // recover on retry. Distinct from llm_5xx (provider HTTP errors)
    // and llm_timeout (deadline exceeded) so users get a clearer fix
    // path ("re-run the task" vs "wait it out" vs "check the model").
    match: /Failed to connect to (serving|model|inference|eval)/i,
    title: "Evaluator service unavailable",
    icon: "solar:server-square-cloud-linear",
    severity: "error",
    hints: () => [
      "The evaluator's internal model serving service was unreachable or closed the connection before responding.",
      "This is almost always transient — re-run the task in a few minutes and the failed spans will be retried.",
      "If it's happening across many tasks at once, the serving cluster may be under load. Lowering the sampling rate temporarily can help while it recovers.",
      "If it persists for more than 30 minutes, contact support with the task ID — it may be an infrastructure incident.",
    ],
    normalize: (stripped) => stripped.slice(0, 200),
  },
  {
    id: "connection_dropped",
    // Catch low-level connection errors that didn't match the more
    // specific service_unavailable category above. Same severity and
    // similar fix path (retry), but the title is more generic since
    // we don't know which downstream dropped.
    match:
      /Connection aborted|RemoteDisconnected|Remote end closed connection|ConnectionResetError|BrokenPipeError|Broken pipe/i,
    title: "Connection dropped mid-request",
    icon: "solar:plug-circle-linear",
    severity: "error",
    hints: () => [
      "An HTTP connection to a downstream service (model provider, evaluator service, or storage) was closed before a response arrived.",
      "Almost always transient — re-run the task and the failed spans will be retried.",
      "If it persists, the most likely culprit is an overloaded downstream service or a misconfigured proxy/load-balancer timeout.",
    ],
    normalize: (stripped) => stripped.slice(0, 200),
  },
  {
    id: "drain_stall",
    // eval_tasks.py emits a single summary entry when a historical
    // task's worker-pool drain stops making progress. The status flips
    // to COMPLETED so the cron can free the slot, but the summary tells
    // the user how many spans never produced a result and how to recover.
    match: /Drain stall:\s*(\d+)\s+of\s+(\d+)/i,
    title: "Partial run — some spans were not evaluated",
    icon: "solar:hourglass-line-linear",
    severity: "error",
    hints: (m) => {
      const missing = m[1];
      const total = m[2];
      return [
        `${missing} of ${total} dispatched evaluations stopped producing results before the task finished.`,
        "Most common cause: the upstream model silently dropped the stream on a large multimodal payload, or a worker was recycled mid-evaluation.",
        "Re-run the task — only the unprocessed spans will be retried, so you won't be charged for the ones that already succeeded.",
        "If the same count keeps getting dropped across runs, lower the sampling rate or trim the mapped multimodal field so fewer heavy payloads are in flight at once.",
      ];
    },
    normalize: () => "Drain stall — some spans were not evaluated",
  },
  {
    id: "empty_response",
    // Agent evaluator raises this when the gateway returns 200 but
    // the LLM produced no content (evaluator.py:1357). Common on
    // large multimodal payloads where the upstream silently drops
    // the stream, or when safety filters block the response without
    // surfacing an error code.
    match:
      /Model returned empty response|upstream.{0,20}(returned |provided )?no content|empty (agent|model|LLM) response/i,
    title: "Model returned empty response",
    icon: "solar:chat-dots-linear",
    severity: "error",
    hints: () => [
      "The evaluator's LLM call completed without returning any content.",
      "This commonly happens with multimodal inputs (audio/image) when the upstream provider silently drops the response or a safety filter blocks it without a clear error.",
      "Re-run the task — empty responses are often transient and clear on retry.",
      "If the same span keeps failing, trim the mapped multimodal field (shorter audio/smaller image) or switch the eval to a different model.",
    ],
    // Strip the per-row "(model=..., iterations=N)" detail so all
    // variants group into a single row in the error log.
    normalize: (stripped) =>
      stripped.replace(/\s*\(model=[^,)]+,\s*iterations=\d+\)/, "").slice(0, 200),
  },
  {
    id: "non_numeric_value",
    // Numeric-similarity / numeric-comparison evals expect a number but
    // got something they couldn't parse. Backend emission sites include:
    //   numeric_similarity.yaml:38 → "No numeric value found in {name}"
    //   functions.py:1011         → "No numeric value found in {name}: '{value}'"
    //   numeric_similarity.yaml:29 → "{name} is None"
    //   numeric_similarity.yaml:34 → "{name} is empty"
    //   numeric_similarity.yaml:47 → "Cannot calculate: <reason>"
    // These all indicate the same root cause: the mapped field's value
    // isn't a number (or is missing entirely) when the eval needs one.
    match: /No numeric value found|is None\b|is empty\b|Cannot calculate:/i,
    title: "Non-numeric value on span",
    icon: "solar:hashtag-square-linear",
    severity: "error",
    hints: () => [
      "The eval needs a numeric value (e.g. a score or count) but the mapped field on the span isn't a number — it's empty, null, or text.",
      "Open the Details tab and check the Variable Mapping for this eval — confirm the mapped field actually contains a number on the matching spans.",
      "Use the Test button to preview a sample span and inspect the value of the mapped field.",
      "If your SDK formats numbers as strings, that's usually fine — but completely empty or null values will trip the parser.",
    ],
    // Strip a trailing single-quoted value (e.g. "...: 'foo'") so a
    // group key isn't bloated with row-specific values.
    normalize: (stripped) => stripped.replace(/: '[^']*'$/, "").slice(0, 160),
  },
];

const GENERIC = {
  id: "generic",
  title: "Unknown error",
  icon: "solar:danger-triangle-linear",
  severity: "error",
  hints: () => [
    "We couldn't auto-classify this error — check the raw message below for clues.",
    "If the eval uses a custom template, rerun it with a single test span to reproduce the failure in isolation.",
    "If it persists, contact support with this task ID and the raw error text.",
  ],
};

/**
 * Classify a single raw error string into a structured result.
 */
export function classifyTaskError(rawMessage) {
  const stripped = stripPrefix(rawMessage);

  for (const def of CATEGORIES) {
    const m = stripped.match(def.match);
    if (m) {
      return {
        category: def.id,
        title: def.title,
        icon: def.icon,
        severity: def.severity,
        hints: def.hints(m),
        normalized: def.normalize(stripped, m),
        raw: rawMessage,
      };
    }
  }

  return {
    category: GENERIC.id,
    title: GENERIC.title,
    icon: GENERIC.icon,
    severity: GENERIC.severity,
    hints: GENERIC.hints(),
    normalized: stripped.slice(0, 160) || "Unknown error",
    raw: rawMessage,
  };
}

/**
 * Enrich a backend-aggregated error group with classifier metadata.
 *
 * The backend returns rows like `{ normalized, count, sample }` — the
 * grouping is already done in SQL. We still need the classifier's
 * category / title / icon / severity / hints, which are derived by
 * regex-matching the sample against the category definitions above.
 *
 * Returns a single shape that matches what the old client-side grouper
 * produced, so `ErrorGroupCard` consumes it unchanged.
 */
export function enrichErrorGroup(group) {
  const sample = group?.sample || group?.normalized || "";
  const classified = classifyTaskError(sample);
  return {
    // Classifier-derived metadata
    category: classified.category,
    title: classified.title,
    icon: classified.icon,
    severity: classified.severity,
    hints: classified.hints,
    // Prefer the backend's normalized string for display — it's what the
    // SQL GROUP BY saw, so users see the exact string that produced the
    // grouping. Fall back to the classifier's normalize() if the backend
    // returned an empty normalized field.
    normalized: group?.normalized || classified.normalized,
    raw: sample || classified.raw,
    count: group?.count || 0,
    // ErrorGroupCard expects an array — we only get one sample per
    // group from the backend (cheap enough to render verbatim).
    examples: sample ? [sample] : [],
  };
}

/**
 * Enrich + sort a list of backend error groups. Backend already sorts
 * by count descending, but we re-sort here to be defensive in case the
 * ordering contract changes, and to break ties by severity (error before
 * warning) for consistent UX.
 */
export function enrichErrorGroups(groups) {
  if (!Array.isArray(groups) || groups.length === 0) return [];
  const severityRank = { error: 0, warning: 1 };
  return groups.map(enrichErrorGroup).sort((a, b) => {
    if (b.count !== a.count) return b.count - a.count;
    return (severityRank[a.severity] ?? 2) - (severityRank[b.severity] ?? 2);
  });
}
