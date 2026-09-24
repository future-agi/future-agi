// Product chart explanations, in dashboard order.
export const CHART_GUIDE = {
  call_success:
    "The single-line answer: what share of tasks the agent actually completed. Everything else on this page tries to explain the delta between this number and 100%. Click either slice to jump straight to the passing or failing tasks.",
  goal_outcome:
    "Splits the run four ways: passed, failed on evaluator, hard-errored (crash / timeout), or escalated to a human. A big amber wedge points at infra / tool problems; a big purple wedge means the agent bailed instead of trying — both are different fixes than a normal failure.",
  sentiment:
    "How the simulated caller sounded by the end of the task. A big negative wedge — even on passing tasks — usually means the agent got the answer right the wrong way (too curt, too slow, too many clarifiers). Pair with disconnection reason to spot rude-but-successful patterns.",
  disconnection:
    "How each task actually ended — completed, escalated to a human, ran out of turns, timed out, or errored. Big Timeout / Error slices are infrastructure smells; big Escalated is an over-cautious agent; big Incomplete is one that gave up mid-task.",
  evaluations:
    "One row per evaluator with its own pass rate. The task's overall pass/fail is an AND across every grader — so a single grader in the red is often the actual bottleneck. Sort your fix work by the grader that's failing hardest.",
  csat: "How many calls landed on each CSAT score from 0 to 10 — the same per-call score as the Avg CSAT tile. Red scores (4 and below) are unhappy callers; a lump on the left means the agent is solving problems in a way callers don't like. The footer checks the provider's own success judgement against your evals, so you know how far to trust it.",
  voice_slos:
    "Latency broken down by the four voice-pipeline segments callers actually feel: Time-to-First-Word, model thinking, text-to-speech, speech-to-text. Any red p90 means callers heard silence past your SLO — that's the one to fix first.",
  pipeline_cost:
    "Per-call spend, split by voice-pipeline stage. If LLM towers over everything, you're overspending on model tokens (shorter prompt, cheaper model, cache). If TTS or STT dominate, look at voice provider tier. Transport bloat usually means calls staying open too long.",
  task_latency:
    "Latency for each task in the order it ran. Random spikes = flaky infra; a steady climb = something the agent is doing more of over time (retries, context growth); a step change = usually a new tool or model kicking in mid-run.",
  percentiles:
    "Every task's end-to-end latency, sorted: read across to a percentile, up to the latency. p50 = typical; p90 = the slower 10% of tasks (the ones your SLO is really written for); p99 = your worst tail. A curve that bends sharply upward near the right edge means a small set of tasks is dragging the tail.",
  response_time:
    "Each call's average time for the agent to start replying after the caller stops talking — the same per-call figure as the Agent Latency tile. Red buckets are at or over the 550ms target, where callers start to notice silence. A second hump on the right usually means one tool or prompt path is consistently slow.",
  distribution:
    "One row per metric with the four numbers that describe its shape. p90 is the number to defend in a review; the max tells you how bad your worst tail actually got. A big gap between p50 and p99 means a few outliers are dragging the run and are worth investigating first.",
  risk: "Ranks the tasks by the use case they exercise (refund, escalation, tool call, etc.) and shows the pass/fail split for each. The use case at the top is the one the agent struggles with most — usually a better fix target than picking off individual failing tasks.",
  tools_volume:
    "How many times the agent called each tool across the run. It shows which tools carry the conversation: a rarely-called tool may be one the agent doesn't know when to use, and a heavily-used one is where a single failure hurts most. Fixes here usually belong to the infra team, not the prompt team.",
  tools_failure:
    "The share of each tool's calls that failed, worst first, with failed / total calls on each bar. Anything past the 40% danger line is breaking the agent's flow — the agent can't reason its way around a broken tool, so route these to infra, not the prompt team.",
  slowest:
    "The eight worst offenders on latency. These are the ones driving your p90 and p99 up — fix one of these and the Latency percentiles curve visibly improves. If the top ones share a persona or use case, you've found a pattern, not a one-off.",
  expensive:
    "The eight tasks that ate the most dollars this run. A handful of expensive tasks usually dominate the total — a shorter prompt on these often saves more than optimising every task. Cross-check with tokens: high cost + high tokens is prompt bloat, high cost + low tokens is a pricey model.",
};
