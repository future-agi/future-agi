import { useQuery } from "@tanstack/react-query";

import {
  CAPABILITY,
  useCapabilities,
  useFeatureAllowed,
} from "src/hooks/useCapabilities";
import axios, { endpoints } from "src/utils/axios";

import { FIX_LIVE_STATES, FIX_POLL_MS } from "./fixRuns";

// A person's own coding agent (Claude Code, Cursor, Codex) fixes a finished run from
// a brief, submits its change with the `fagi` CLI, and Future AGI checks it as a short
// Epoch: an attempt. The backend setting turns all of it on; the capabilities read
// carries it, so nothing here probes a route that answers 404 while it is off.
export function useCodingAgentAttemptsEnabled() {
  const { data, isLoading, isError } = useCapabilities();
  const { allowed } = useFeatureAllowed(CAPABILITY.FIX_MY_AGENT);
  return (
    allowed &&
    !isLoading &&
    !isError &&
    data?.coding_agent_attempts_enabled === true
  );
}

// An Epoch a coding agent's submitted change started. The first attempt has no
// attempt_of_id, so the request's outside candidate is the mark.
export const isOutsideAttempt = (fix) =>
  fix?.request?.outside_candidate?.number != null;

export async function getCodingAgentBrief(sourceRunId) {
  const res = await axios.get(endpoints.agentFixRuns.codingAgentBrief(), {
    params: { source_run_id: sourceRunId },
  });
  return res.data;
}

// The setup a coding agent needs before it can follow the brief: the CLI, its login,
// and, if it wants the brief and the verdict as tools, the MCP server.
export function codingAgentSetup(setupUrl) {
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
export const codingAgentPrompt = (brief, setupUrl) =>
  `${codingAgentSetup(setupUrl)}\n\n---\n\n${brief.instructions}`;

export const attemptVerdictKey = (fixId) => [
  "agent-fix-attempt-verdict",
  fixId,
];

export async function getAttemptVerdict(fixId) {
  const res = await axios.get(endpoints.agentFixRuns.verdict(fixId));
  return res.data;
}

// An outside attempt's verdict, as its coding agent reads it: each check's status,
// and detail only on the checks that ran on cases the agent was given.
export function useAttemptVerdict(fix, { enabled }) {
  const { data, isLoading, error } = useQuery({
    queryKey: attemptVerdictKey(fix?.id),
    queryFn: () => getAttemptVerdict(fix.id),
    enabled: enabled && isOutsideAttempt(fix),
    refetchInterval: (query) =>
      FIX_LIVE_STATES.has(query.state.data?.state ?? fix?.state)
        ? FIX_POLL_MS
        : false,
    meta: { errorHandled: true },
  });
  return { verdict: data, isLoading, error };
}
