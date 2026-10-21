import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { proofKindOf, isComingSoon, COMING_SOON_NOTE, platformTestingOf, numbersPinned } from "../_mock/rsiModel";
import { replicationOf } from "../_mock/replication";

/**
 * The world of a connect-only environment: there isn't one.
 *
 * A phone agent that books into its own systems has no tables we seed and no
 * tool handlers we run, so there is nothing to reset between scenarios. Its
 * tools are in the contract only; the one thing every scenario shares is the
 * caller account it books under — and that account's credentials are agent
 * configuration, supplied per run (S8), not part of the suite.
 *
 * Shared by the World tab and Overview so both say it the same way.
 */
export default function ConnectOnlyWorld({ env, sx }) {
  const tools = env?.tools || [];
  const accounts = env?.fixtures?.callerAccounts ?? 1;
  const rows = [
    {
      label: `Tools · ${tools.length}`,
      value: (
        <>
          In the contract only —{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", color: "text.primary" }}>
            {tools.map((t) => t.name).join(", ")}
          </Box>
          . Your systems answer them; every sub-goal is judged from the call.
        </>
      ),
    },
    {
      label: "Fixtures",
      value: `${accounts} caller account${accounts === 1 ? "" : "s"} shared by every scenario — its credentials are agent configuration, supplied per run (not part of the suite), so rotating them doesn't outdate a scenario.`,
    },
    {
      /* PRD v6 Story 5 — the dial chooses how *our* world answers a tool.
         Here nothing of ours answers, so there is nothing to set. */
      label: "Tool fidelity",
      value: "Doesn't apply — your systems answer every call, so there's no tier or fault profile to set.",
    },
    {
      label: "Proof",
      value: "Each scenario is proved against a reference transcript rather than a reference solution of tool calls.",
    },
  ];

  /* Built from a description and no agent yet: not a phone agent calling its
     own systems — there's simply nothing to read until one is connected. */
  const awaitingAgent = env?.builtFrom?.kind === "scratch" && !(env?.tools || []).length;
  const shown = awaitingAgent ? [
    { label: "Tools", value: "None yet — read from your agent when you connect it." },
    { label: "Data", value: "None yet — seeded from what the agent's tools need, once it's connected." },
    { label: "Proof", value: "Each scenario has a reference outcome and sub-goals; it's proved when the agent's tools are known." },
  ] : rows;

  return (
    <SectionCard
      title={awaitingAgent ? "No world yet" : "No world to reset"}
      subtitle={awaitingAgent
        ? "Built from your description — the world is set up when you connect your agent"
        : "This phone agent calls its own systems, so there is no seeded data to restore between scenarios"}
      sx={sx}
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {shown.map((r) => (
          <Box
            key={r.label}
            sx={{ display: "grid", gridTemplateColumns: "120px minmax(0, 1fr)", columnGap: 2, px: 2.5, py: 1.25 }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{r.label}</Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{r.value}</Typography>
          </Box>
        ))}
      </Stack>
    </SectionCard>
  );
}
ConnectOnlyWorld.propTypes = { env: PropTypes.object, sx: PropTypes.object };

/* A quiet note — results moved, nothing we version did. Not an alarm. */
export function ResultsMovedNote({ move, sx }) {
  if (!move) return null;
  return (
    <Stack
      direction="row" spacing={1} alignItems="flex-start"
      sx={{ px: 2, py: 1.25, borderRadius: 1.5, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral", ...sx }}
    >
      <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
      <Box minWidth={0}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{move.note}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, fontVariantNumeric: "tabular-nums" }}>
          {`Run ${move.from.ordinal} → run ${move.to.ordinal} · same agent, environment, suite, sub-goals, evals and caller`}
        </Typography>
      </Box>
    </Stack>
  );
}
ResultsMovedNote.propTypes = { move: PropTypes.object, sx: PropTypes.object };

/*
  Sealed or Live (RSI PRD "Environments, defined") — said once where the
  world is described, on the World tab and Overview. Which one decides how
  much a pass here proves: sealed is checked against the world's end state,
  live only against the starting state and a model's reading of the
  conversation, prompt only without the agent's tools at all.
*/
const KIND_COPY = {
  sealed: {
    icon: "solar:lock-keyhole-minimalistic-linear",
    line: "Sealed — the agent's tool calls are answered by this environment's world inside our sandbox. Sub-goals are checked against the world's end state and the agent's tool calls.",
  },
  live: {
    icon: "solar:plug-circle-linear",
    line: "Live — the agent runs in your account and its tools reach your real systems. Only the starting state is checked, and a model judges sub-goals from the conversation.",
  },
  "prompt-only": {
    icon: "solar:document-text-linear",
    line: "Prompt only — checked by running the prompt without your tools.",
  },
};

/* "test copy" → "a test copy"; leaves "your agent" or "the draft" alone. */
const withArticle = (phrase) => (/^(a|an|the|your)\b/i.test(phrase) ? phrase : `${/^[aeiou]/i.test(phrase) ? "an" : "a"} ${phrase}`);

export function EnvironmentKindNote({ env, envState, sx }) {
  const agent = envState?.agent;
  const soon = isComingSoon(env);
  if (!agent && !soon) return null;
  const pk = agent ? proofKindOf(agent) : null;
  const copy = pk ? KIND_COPY[pk.kind] : null;
  const rep = agent ? replicationOf(agent, env) : null;
  const unpinned = agent && platformTestingOf(agent)?.needsPinnedNumbers && !numbersPinned(agent);
  /* One line: what kind of proof this is and what a run calls. The full
     reading — what's checked, how the copy is made — is on hover. Consent is
     asked for where it's given (Needs input), not here. */
  const short = pk?.kind === "live"
    ? `runs call ${rep ? withArticle(rep.label.charAt(0).toLowerCase() + rep.label.slice(1)) : "your agent"} in your account; its tools reach your real systems`
    : pk?.kind === "sealed" ? "tool calls are answered by this world, inside our sandbox"
      : pk ? "the prompt runs without your tools" : null;
  const tip = [
    copy?.line,
    rep?.how,
    unpinned ? "Your Retell numbers aren't pinned to a released version, so tests use settings overridden for one call until they are." : null,
  ].filter(Boolean).join(" ");
  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={{ px: 0.5, ...sx }}>
      <Iconify icon={copy?.icon || "solar:clock-circle-linear"} width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>
        {pk && <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>{pk.label}</Box>}
        {short && ` — ${short}`}
        {soon && `${pk ? " · " : ""}${COMING_SOON_NOTE}`}
      </Typography>
      {tip && (
        <Tooltip arrow title={tip}>
          <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
            <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.disabled" }} />
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}
EnvironmentKindNote.propTypes = { env: PropTypes.object, envState: PropTypes.object, sx: PropTypes.object };
