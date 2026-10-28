// The numbers behind each analytics widget, one object per CSV row, read
// straight from the run analytics payload (no rounding, no "-" for missing).
const segmentRows = (breakdown) =>
  (breakdown?.segments || []).map((segment) => ({
    segment: segment.label,
    count: segment.count,
    share: segment.share,
  }));

const binRows = (data, key) =>
  (data?.bins || []).map((bin) => ({
    [key]: bin.label,
    lower: bin.lower,
    upper: bin.upper,
    count: bin.count,
  }));

export function widgetRows(id, { data, noun = "call" }) {
  const dashboard = data?.dashboard || {};
  const breakdown = (key) =>
    (dashboard.breakdowns || []).find((item) => item.key === key);
  const series = dashboard.series || [];
  const step = dashboard.series_mode === "time_buckets" ? "time_bucket" : noun;
  switch (id) {
    case "goal_outcome":
    case "disconnection":
    case "provider_success":
    case "sentiment":
      return segmentRows(breakdown(id));
    case "reliability":
      return (data?.reliability?.rows || []).map((row) => ({
        scenario: row.scenario,
        verdict: row.verdict,
        passed: row.passed,
        evaluated: row.evaluated,
        errored: row.error,
        inconclusive: row.inconclusive,
      }));
    case "evaluations":
      return (data?.evaluations || []).map((row) => ({
        eval: row.name,
        pass_rate: row.pass_rate,
        passed: row.passed,
        evaluated: row.measured,
        could_not_run: row.errored,
        not_applicable: row.missing,
      }));
    case "csat":
      return binRows(dashboard.csat, "score");
    case "response_time":
      return binRows(dashboard.agent_response_time, "bucket");
    case "voice_slos":
      return (dashboard.voice_slos || []).map((row) => ({
        segment: row.label,
        measured: row.measured,
        p50_ms: row.p50,
        p90_ms: row.p90,
        p99_ms: row.p99,
      }));
    case "pipeline_cost":
      return series.map((row) => ({
        [step]: row.label,
        llm_cents: row.llm_cents,
        tts_cents: row.tts_cents,
        stt_cents: row.stt_cents,
        storage_cents: row.storage_cents,
      }));
    case "call_duration":
      return series.map((row) => ({
        [step]: row.label,
        duration_ms: row.duration_ms,
      }));
    case "duration_percentiles":
      return (dashboard.latency_percentiles || []).map((row) => ({
        percentile: row.percentile,
        duration_ms: row.value,
      }));
    case "task_latency":
      return series.map((row) => ({
        [step]: row.label,
        latency_ms: row.latency_ms,
      }));
    case "percentiles":
      return (dashboard.agent_latency_percentiles || []).map((row) => ({
        percentile: row.percentile,
        latency_ms: row.value,
      }));
    case "distribution":
      return (dashboard.distributions || [])
        .filter((row) => row.key !== "end_to_end_ms")
        .map((row) => ({
          metric: row.key,
          measured: row.measured,
          p50: row.p50,
          p90: row.p90,
          p99: row.p99,
          max: row.max,
        }));
    case "risk":
      return dashboard.use_case_risk || [];
    case "tools_volume":
      return (dashboard.tools?.volume || []).map((row) => ({
        tool: row.name,
        invocations: row.invocations,
      }));
    case "tools_failure":
      return (dashboard.tools?.failures || []).map((row) => ({
        tool: row.name,
        failure_rate: row.failure_rate,
        failures: row.failures,
        measured: row.measured,
      }));
    case "slowest":
    case "expensive":
      return (
        (id === "slowest"
          ? dashboard.slowest_tasks
          : dashboard.most_expensive_tasks) || []
      ).map((row) => ({
        rank: row.rank,
        scenario: row.label,
        [id === "slowest" ? "duration_seconds" : "cost_cents"]: row.value,
        [`${noun}_id`]: row.id,
      }));
    default:
      return [];
  }
}
