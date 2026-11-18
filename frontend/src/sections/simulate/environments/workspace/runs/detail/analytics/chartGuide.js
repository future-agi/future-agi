// Product chart explanations, in dashboard order.
export const CHART_GUIDE = {
  goal_outcome:
    "Splits the run into passed, failed on an eval, errored (crash / timeout / infrastructure), escalated to a human, and not evaluated. Only passed and failed calls judge the agent; errors are shown so infrastructure problems stay visible without lowering the pass rate. Click a slice to open those calls.",
  disconnection:
    "How each call ended, with every provider's end reason mapped to one list: completed, caller hung up, agent ended, transferred, silence timeout, time or turn limit, voicemail, error. A reason the list doesn't recognise is shown as Unrecognised rather than guessed.",
  provider_success:
    "The voice provider's own successful / unsuccessful judgement, shown only when the provider reports it. It is a second opinion next to your evals, never a replacement for them.",
  sentiment:
    "The provider's own sentiment label for the caller, shown only when the provider reports it. The platform does not compute sentiment itself.",
  reliability:
    "Each scenario ran once per trial with nothing changed, so different results across trials are the agent's own variance. A scenario that flips is unreliable even when its average looks fine. The 95% range on the pass rate accounts for trials of one scenario not being independent, so more trials of the same scenarios narrow it less than more scenarios would.",
  evaluations:
    "One row per eval with its own pass rate over the calls it evaluated. A call passes only when every eval that ran on it passed, so the eval failing hardest is usually the bottleneck. Could not run means the evaluator itself failed; not applicable means the eval did not apply to that call's scenario. Neither counts as a fail.",
  csat: "How many calls landed on each CSAT score from 0 to 10, the same per-call score as the Avg CSAT tile. Red scores (4 and below) are unhappy callers. A provider's 0/1 success flag is never mixed into this scale. The footer compares the provider's own success judgement with your evals when the provider reports one.",
  voice_slos:
    "Latency broken down by the voice-pipeline segments callers feel: model thinking, text-to-speech, and speech-to-text. Any red p90 means callers heard silence past your target. Time to first word is not recorded yet.",
  pipeline_cost:
    "Per-call spend, split by voice-pipeline stage. If LLM towers over everything, you're overspending on model tokens (shorter prompt, cheaper model, cache). If TTS or STT dominate, look at voice provider tier. Transport bloat usually means calls staying open too long.",
  task_latency:
    "Each call's average agent response time in the order it ran. Random spikes point at flaky infrastructure; a steady climb means the agent is doing more of something over time (retries, context growth); a step change usually means a new tool or model kicked in mid-run.",
  percentiles:
    "Every call's average agent response time, sorted: read across to a percentile, up to the wait. p50 is typical, p90 is the slower 10% of calls. These are per-call averages, so a single long pause inside an otherwise quick call does not show here.",
  response_time:
    "Each call's average time for the agent to start replying after the caller stops talking. Red buckets are at or over the target (1.5 s for voice, 3 s for chat), where callers start to notice silence. A second hump on the right usually means one tool or prompt path is consistently slow.",
  distribution:
    "One row per metric with the four numbers that describe its shape. p90 is the number to defend in a review; the max tells you how bad your worst tail actually got. A big gap between p50 and p99 means a few outliers are dragging the run and are worth investigating first.",
  risk: "Scenarios ranked by pass rate, weakest first, with the pass/fail/errored split for each. Scenarios with fewer than 3 evaluated calls are ranked last because their rate is too noisy to act on.",
  tools_volume:
    "How many times the agent called each tool across the run. It shows which tools carry the conversation: a rarely-called tool may be one the agent doesn't know when to use, and a heavily-used one is where a single failure hurts most. Fixes here usually belong to the infra team, not the prompt team.",
  tools_failure:
    "The share of each tool's calls that failed, worst first, with failed / total calls on each bar. Anything past the 40% danger line is breaking the agent's flow. The agent can't reason its way around a broken tool, so route these to infra, not the prompt team.",
  slowest:
    "The eight longest calls. These drive the duration p90 and p99. If the top ones share a scenario, you've found a pattern, not a one-off.",
  expensive:
    "The eight calls that cost the most this run. A handful of expensive calls usually dominate the total. Cross-check with tokens: high cost with high tokens is prompt bloat; high cost with low tokens is a pricey model.",
};
