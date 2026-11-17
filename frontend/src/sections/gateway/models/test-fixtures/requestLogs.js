// A stand-in for GET /agentcc/request-logs/ over a list of log rows, with the
// filters the Django list applies (agentcc/views/request_log.py): model and
// provider as comma-separated "any of", is_error, started_after, ordering by
// latency_ms, and page/limit pagination with `count`. A page past the end
// answers 404, as DRF's PageNumberPagination does.

const csv = (value) =>
  value == null || value === ""
    ? []
    : String(value)
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);

const truthy = (value) => ["true", "1"].includes(String(value).toLowerCase());

export function fakeRequestLogs(rows) {
  return (config = {}) => {
    const params = config.params || {};
    let list = rows.slice();
    const models = csv(params.model);
    if (models.length) list = list.filter((r) => models.includes(r.model));
    const providers = csv(params.provider);
    if (providers.length)
      list = list.filter((r) => providers.includes(r.provider));
    const modes = csv(params.billing_mode);
    if (modes.length) list = list.filter((r) => modes.includes(r.billing_mode));
    if (params.is_error != null && params.is_error !== "") {
      const want = truthy(params.is_error);
      list = list.filter((r) => Boolean(r.is_error) === want);
    }
    if (params.started_after) {
      const since = Date.parse(params.started_after);
      list = list.filter((r) => Date.parse(r.started_at) >= since);
    }
    if (params.ordering === "latency_ms") {
      list.sort((a, b) => a.latency_ms - b.latency_ms);
    } else if (params.ordering === "-latency_ms") {
      list.sort((a, b) => b.latency_ms - a.latency_ms);
    }
    const limit = Number(params.limit) || 10;
    const page = Number(params.page) || 1;
    const totalPages = Math.max(1, Math.ceil(list.length / limit));
    if (page > totalPages) {
      const error = new Error("Invalid page.");
      error.response = { status: 404 };
      throw error;
    }
    return {
      count: list.length,
      next: null,
      previous: null,
      results: list.slice((page - 1) * limit, page * limit),
      total_pages: totalPages,
      current_page: page,
    };
  };
}

const minutesAgo = (n) => new Date(Date.now() - n * 60000).toISOString();

const log = (
  model,
  provider,
  latency,
  { error = false, age = 30, mode = "byok" } = {},
) => ({
  model,
  provider,
  billing_mode: mode,
  latency_ms: latency,
  is_error: error,
  started_at: minutesAgo(age),
});

// openai/gpt-4o on the demo stack: one model, three upstreams. Managed
// requests go out as "openai/gpt-4o" through openrouter; the org's own OpenAI
// key serves "gpt-4o" (and the catalog id, when a caller sends that); the
// flaky-upstream provider also serves "gpt-4o" but is not a route of this
// catalog model. Its 10 requests outnumber the OpenAI key's, so
// analytics/model-comparison names flaky-upstream as gpt-4o's provider.
export const GPT_4O_LOGS = [
  ...[8, 8, 8, 9, 10, 32].map((ms) =>
    log("openai/gpt-4o", "openrouter", ms, { mode: "managed" }),
  ),
  log("gpt-4o", "openai", 100),
  log("gpt-4o", "openai", 120),
  log("gpt-4o", "openai", 130, { error: true }),
  log("gpt-4o", "openai", 400),
  log("openai/gpt-4o", "openai", 110),
  ...Array.from({ length: 10 }, (_, i) =>
    log("gpt-4o", "flaky-upstream", 1500 + i, { error: i < 6 }),
  ),
  // Older than the 24 h window.
  log("gpt-4o", "openai", 9000, { age: 30 * 60 }),
];
