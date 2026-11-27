import { useQuery } from "@tanstack/react-query";

import type {
  AgentFixAttemptVerdictApi,
  AgentFixCodingAgentBriefApi,
  AgentFixRunApi,
  CapabilitiesResponseApi,
} from "src/generated/api-contracts/api.schemas";
import {
  CAPABILITY,
  useCapabilities,
  useFeatureAllowed,
} from "src/hooks/useCapabilities";
import axios, { endpoints } from "src/utils/axios";

import { FIX_LIVE_STATES, FIX_POLL_MS } from "./fixRuns";

// An Epoch as the attempt reads take it: its list read and its detail read both fit.
type AttemptFix = Pick<AgentFixRunApi, "id" | "state" | "request">;

// A person's own coding agent (Claude Code, Cursor, Codex) fixes a finished run from
// a brief, submits its change with the `fagi` CLI, and Future AGI checks it as a short
// Epoch: an attempt. The backend setting turns all of it on; the capabilities read
// carries it, so nothing here probes a route that answers 404 while it is off.
export function useCodingAgentAttemptsEnabled(): boolean {
  const { data, isLoading, isError } = useCapabilities();
  const capabilities: CapabilitiesResponseApi | undefined = data;
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  return (
    allowed &&
    !isLoading &&
    !isError &&
    capabilities?.coding_agent_attempts_enabled === true
  );
}

// An Epoch a coding agent's submitted change started. The first attempt has no
// attempt_of_id, so the request's outside candidate is the mark.
export const isOutsideAttempt = (
  fix: Pick<AgentFixRunApi, "request"> | null | undefined,
): boolean => fix?.request?.outside_candidate?.number != null;

export async function getCodingAgentBrief(
  sourceRunId: string,
): Promise<AgentFixCodingAgentBriefApi> {
  const res = await axios.get(endpoints.agentFixRuns.codingAgentBrief(), {
    params: { source_run_id: sourceRunId },
  });
  return res.data;
}

// The setup a coding agent needs before it can follow the brief: the CLI, its login,
// and, if it wants the brief and the verdict as tools, the MCP server.
export function codingAgentSetup(setupUrl: string): string {
  return [
    "## Set up Future AGI first",
    "",
    "Skip this if the `fagi` command already works here.",
    "",
    "1. Install the Future AGI CLI: `npm install -g @future-agi/cli`. To run it without installing, use `npx @future-agi/cli` in place of `fagi` in each command.",
    "2. Sign in: `fagi login`.",
    `3. Optional: add the Future AGI MCP server to your coding agent, so it can read this brief and the verdict as tools. The steps for Claude Code, Cursor and Codex are at ${setupUrl}.`,
  ].join("\n");
}

// What "Copy for coding agent" copies: the setup, then the brief's own instructions
// exactly as the platform wrote them.
export const codingAgentPrompt = (
  brief: Pick<AgentFixCodingAgentBriefApi, "instructions">,
  setupUrl: string,
): string => `${codingAgentSetup(setupUrl)}\n\n---\n\n${brief.instructions}`;

export const attemptVerdictKey = (fixId: string | undefined) =>
  ["agent-fix-attempt-verdict", fixId] as const;

export async function getAttemptVerdict(
  fixId: string,
): Promise<AgentFixAttemptVerdictApi> {
  const res = await axios.get(endpoints.agentFixRuns.verdict(fixId));
  return res.data;
}

interface AttemptVerdictRead {
  verdict: AgentFixAttemptVerdictApi | undefined;
  isLoading: boolean;
  error: Error | null;
  // Read the verdict again, after a read that failed.
  refetch: () => Promise<unknown>;
}

// An outside attempt's verdict, as its coding agent reads it: each check's status,
// and detail only on the checks that ran on cases the agent was given.
export function useAttemptVerdict(
  fix: AttemptFix | null | undefined,
  { enabled }: { enabled: boolean },
): AttemptVerdictRead {
  const fixId = fix?.id;
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: attemptVerdictKey(fixId),
    // Only enabled with an id (an outside attempt is a stored Epoch).
    queryFn: () => getAttemptVerdict(fixId as string),
    enabled: enabled && isOutsideAttempt(fix),
    refetchInterval: (query) =>
      FIX_LIVE_STATES.has(query.state.data?.state ?? fix?.state ?? "")
        ? FIX_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
  return { verdict: data, isLoading, error, refetch };
}

// The most outside attempts a run takes: the API's cap on an attempt's number
// (OUTSIDE_ATTEMPTS_MAX); codingAgent.contract.test.ts holds the two equal.
export const OUTSIDE_ATTEMPTS_MAX = 4;

// The run's last attempt: one that counts, numbered at the cap. Its coding agent can
// submit nothing more on the run; the next step is a new run.
export const usedTheRunsLastAttempt = (
  number: number | undefined,
  counted: boolean | null | undefined,
): boolean =>
  counted === true && number != null && number >= OUTSIDE_ATTEMPTS_MAX;

// The most credits an attempt's budget takes: the API's cap on budget_credits
// (BUDGET_CREDITS_MAX); codingAgent.contract.test.ts holds the two equal.
export const BUDGET_CREDITS_MAX = 50_000;

// What to do once an attempt is at that cap: a larger budget is refused.
export const RUN_FEWER_SCENARIOS =
  "Run the environment on fewer scenarios, then fix the new run.";

export const RUN_AGAIN = `This run has had its ${OUTSIDE_ATTEMPTS_MAX} attempts. Run the environment again, then fix the new run.`;

interface EndedAttempt {
  state: string;
  number: number | undefined;
  counted: boolean | null | undefined;
  budgetCredits: number | undefined;
}

// The next step after an attempt that ended out of budget, past its time limit or on
// our side: the same in its ending and under its checks. At the budget cap a larger
// budget is refused, so the run has to shrink. A counted attempt's tree was checked,
// and the platform refuses the same tree again.
export function attemptNextStep({
  state,
  number,
  counted,
  budgetCredits,
}: EndedAttempt): string {
  if (usedTheRunsLastAttempt(number, counted)) return RUN_AGAIN;
  if (state === "out_of_budget" && (budgetCredits ?? 0) >= BUDGET_CREDITS_MAX) {
    return RUN_FEWER_SCENARIOS;
  }
  if (counted) return "Change the fix, then submit again.";
  return state === "out_of_budget"
    ? "Submit it again with a larger budget: fagi epoch submit --budget-credits."
    : "Submit it again with fagi epoch submit.";
}
