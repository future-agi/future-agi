import React, { useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  Box,
  IconButton,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CodeBlock from "src/components/code-block";
import { pythonTrain, serveCmd, INSTALL_CMD } from "../environments/fromCode";
import SideDrawer from "../components/SideDrawer";
import LocalSteps from "../environments/LocalSteps";
import { CopyField } from "../components/primitives";
import {
  RATE_PLAN,
  WORKSPACE_POLICY,
  estimateTrainingPlan,
  fmtCredits,
  rangeLabel,
  usdRange,
} from "../_mock/pricing";

/* What `--dry-run` prints: the run plan's scope, estimate and limit, in the
   same units and rates — nothing runs. */
const dryRunOutput = (e, budget, repeats) => {
  const unit = e.voice ? "calls" : "trials";
  const ext = e.external[0];
  return [
    `Scope     ${e.trials / repeats / e.versionCount} scenarios × ${repeats} attempts${e.versionCount > 1 ? ` × ${e.versionCount} versions` : ""} = ${e.trials} ${unit}`,
    ...e.lines.map(
      (l) =>
        `  ${l.label.padEnd(30)}${l.status === "priced" ? rangeLabel(l.low, l.high) : l.status === "unpriced" ? "no price set" : l.status === "informational" ? "measured, not billed" : "included"}`,
    ),
    `Estimate  ≈ ${rangeLabel(e.platform.low, e.platform.high, e.platform.unknown)}`,
    ext
      ? `External  ${ext.label}: ${ext.low == null ? "unknown" : usdRange(ext.low, ext.high)} · ${ext.detail.toLowerCase()}`
      : null,
    `Limit     ${fmtCredits(budget)} · up to ${fmtCredits(WORKSPACE_POLICY.maxRunCredits)} per run`,
    `Rates     ${e.rateLine}`,
    e.blocked.length ? `Held      ${e.blocked[0].title}` : "Nothing ran.",
  ]
    .filter(Boolean)
    .join("\n");
};

/*
  Run from code — the same run the Run simulation button starts, from the
  terminal, Python, CI or the developer's own agent (MCP). Every snippet
  carries this environment's ID and the repeats/parallel the header is set
  to, so what you copy runs exactly what you'd run here.

  Also says where the environment is managed: in the console (edits here make
  versions; `fai env pull` exports it to code) or in code (versions come from
  `fai env push`; console edits become a draft to merge).
*/

function Label({ children }) {
  return (
    <Typography
      sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500, mb: 0.75 }}
    >
      {children}
    </Typography>
  );
}

Label.propTypes = { children: PropTypes.node };

const TABS = [
  { id: "cli", label: "CLI" },
  { id: "python", label: "Python" },
  { id: "node", label: "Node" },
  { id: "ci", label: "CI" },
  { id: "mcp", label: "Your agent (MCP)" },
  { id: "train", label: "Train (RL)" },
];

export default function RunFromCodeDrawer({
  open,
  onClose,
  env,
  agentName,
  repeats = 3,
  parallel = 20,
  /* Set when this run is over 200 conversations: it runs this many at once
     (Conversations at once) — the same as the Run button. */
  batchAt = null,
  managedIn = "console",
  repo,
  onPulled,
  /* The run's spending limit in credits — the run plan's default for this
     scope; code runs take the same limit. */
  budget = 1000,
  /* The run plan's estimate for this scope (estimateRunPlan) — what
     --dry-run prints, so the two read the same. */
  estimate = null,
  /* The environment's evals, resolved — the held-out evaluation's judges. */
  trainEvals = [],
}) {
  const [tab, setTab] = useState("cli");
  const id = env?.id || "env_…";
  const flags = `--repeats ${repeats} --parallel ${batchAt || parallel}`;

  const cli = [
    {
      title: "Install and sign in",
      body: "Once per machine. Sign-in opens the browser; in CI use FI_API_KEY.",
      cmd: INSTALL_CMD,
    },
    {
      title: "Preview the cost",
      body: "The same scope, estimate and limit check as the run plan — nothing runs.",
      cmd: `fai sim run ${id} ${flags} --budget ${budget} --dry-run`,
      after: estimate && (
        <Box sx={{ mt: 1 }}>
          <CodeBlock code={dryRunOutput(estimate, budget, repeats)} />
        </Box>
      ),
    },
    {
      title: "Run this environment",
      body: `Runs every scenario against ${agentName ? `the agent at ${agentName}` : "its agent"} — the same run as Run simulation${batchAt ? `; it's over 200 conversations, so ${batchAt} at once` : ""}. No new calls start past --budget. It prints the run's ID and its link here.`,
      hint: "--scenarios picks a subset; --agent-url tests a local build.",
      cmd: `RUN_ID=$(fai sim run ${id} ${flags} --budget ${budget} --print-id)`,
    },
    {
      title: "Read the results",
      body: "Pass rate, the failing scenarios and why, with a link to the run here — the same run, by its ID.",
      cmd: "fai sim results --run $RUN_ID --failures",
    },
    {
      title: "Check what it cost",
      body: "The run's itemized credits, free-tier adjustment and what's pending on your own accounts — the same lines as its Cost tab.",
      hint: `usage show --group-by agent compares versions; admins set limits with usage budget set. A --budget over your admin's ${fmtCredits(WORKSPACE_POLICY.maxRunCredits)} per run is refused here too.`,
      cmd: "fai usage show --run $RUN_ID",
    },
  ];

  const python = `from fi.simulate import Environment

env = Environment.get("${id}")

plan = env.run(repeats=${repeats}, budget=${budget}, dry_run=True)
print(plan.trials, plan.estimate)     # same estimate as the run plan

run = env.run(repeats=${repeats}, parallel=${parallel}, budget=${budget})
for result in run.stream():          # each conversation as it ends
    print(result.scenario.slug, result.passed, result.score)

summary = run.wait()                 # pass rate, failures; run.url opens it here
print(summary.pass_rate, run.url)`;

  const pythonOwnAgent = `from fi.simulate import AgentInput, AgentResponse

def my_agent(input: AgentInput) -> AgentResponse:
    # call your framework here — OpenAI Agents SDK, LangGraph, CrewAI…
    reply, calls = call_my_framework(input.messages)
    return AgentResponse(content=reply, tool_calls=calls)

run = env.run(agent=my_agent, repeats=${repeats})`;

  /* The training plan's dry run — rollouts and held-out evaluation priced
     like a run, the trainer's own cost said as unknown, the budget applied. */
  const trainPlan = (() => {
    const budgetCredits = Math.min(5000, WORKSPACE_POLICY.maxRunCredits);
    const p = estimateTrainingPlan({
      env,
      episodes: 1000,
      heldOut: 100,
      evals: trainEvals,
      budget: budgetCredits,
    });
    const line = (x) =>
      `  ${x.label.padEnd(22)}${x.credits == null ? "unknown" : `${Math.round(x.credits).toLocaleString()} credits`}   ${x.detail}`;
    return [
      ...p.parts.map(line),
      `Estimate  ≈ ${Math.round(p.total).toLocaleString()} credits + training (unknown)`,
      `Budget    ${budgetCredits.toLocaleString()} credits${p.episodesInBudget < 1000 ? ` — covers about ${p.episodesInBudget.toLocaleString()} of 1,000 episodes; the rest won't start, and the rollouts done are kept` : ""}`,
      `Rates     ${RATE_PLAN.name} · ${RATE_PLAN.version} · 1 credit = $${RATE_PLAN.creditUsd.toFixed(2)}`,
      "Nothing ran.",
    ].join("\n");
  })();

  const node = `import { Environment } from "@futureagi/simulate";

const env = await Environment.get("${id}");

const plan = await env.run({ repeats: ${repeats}, budget: ${budget}, dryRun: true });
console.log(plan.trials, plan.estimate);  // same estimate as the run plan

const run = await env.run({ repeats: ${repeats}, parallel: ${parallel}, budget: ${budget} });
const summary = await run.wait();         // pass rate, failures, credits used
console.log(summary.passRate, summary.usage.credits, run.url);`;

  const ci = `# .github/workflows/simulate.yml
name: Simulate
on: [pull_request]
jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install futureagi
      # CI needs a limit: a run without one fails before it starts.
      - run: fai sim run ${id} ${flags} --budget ${budget} --fail-under 0.9
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}`;

  const prompt = `Run ${env?.name || "this environment"} with ${repeats} repeats and summarise what failed and why.`;

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 640 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2.5, py: 2, borderBottom: 1, borderColor: "divider" }}
        >
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{ fontSize: 16, lineHeight: "24px", fontWeight: 600 }}
            >
              Run from code
            </Typography>
            <Typography
              sx={{ fontSize: 13, lineHeight: "20px", color: "text.tertiary" }}
            >
              The same run as Run simulation, from your terminal, code, CI or
              your own agent — or drive it from a training loop.
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={16} />
          </IconButton>
        </Stack>

        <Box sx={{ px: 2.5, py: 2, flex: 1, minHeight: 0, overflowY: "auto" }}>
          <Stack spacing={2.5}>
            {/* Where it's managed — decides whether console edits make versions
                or a draft to merge. */}
            <Stack
              direction="row"
              spacing={1.25}
              sx={{
                p: 1.5,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Iconify
                icon={
                  managedIn === "code"
                    ? "solar:code-square-linear"
                    : "solar:widget-linear"
                }
                width={16}
                sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
              />
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500 }}
                >
                  {managedIn === "code" ? "Managed in code" : "Managed here"}
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", fontWeight: 400 }}
                  >
                    {" · "}
                    <Box
                      component="span"
                      sx={{ fontFamily: "monospace", fontSize: 12 }}
                    >
                      {id}
                    </Box>
                  </Box>
                </Typography>
                <Typography
                  sx={{
                    fontSize: 13,
                    lineHeight: "20px",
                    color: "text.tertiary",
                  }}
                >
                  {managedIn === "code" ? (
                    <>
                      Versions come from{" "}
                      <Box
                        component="span"
                        sx={{ fontFamily: "monospace", fontSize: 12 }}
                      >
                        fai env push
                      </Box>
                      {repo ? ` in ${repo}` : ""}. Changes made here become a
                      draft you merge into the repo.
                    </>
                  ) : (
                    <>
                      Edits here make new versions. To keep it in your repo,
                      pull it into your project — from then on it&apos;s managed
                      in code.
                    </>
                  )}
                </Typography>
                {managedIn !== "code" && (
                  <Box sx={{ mt: 1 }}>
                    <CopyField value={`fai env pull ${id}`} onCopy={onPulled} />
                  </Box>
                )}
              </Box>
            </Stack>

            <ToggleButtonGroup
              exclusive
              size="small"
              value={tab}
              onChange={(_, v) => v && setTab(v)}
              aria-label="Run from"
            >
              {TABS.map((t) => (
                <ToggleButton key={t.id} value={t.id}>
                  {t.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>

            {tab === "cli" && <LocalSteps steps={cli} />}

            {tab === "python" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Run it and stream the results</Label>
                  <CodeBlock code={python} />
                </Box>
                <Box>
                  <Label>
                    Or test your agent from code, without deploying it
                  </Label>
                  <CodeBlock code={pythonOwnAgent} />
                </Box>
              </Stack>
            )}

            {tab === "node" && (
              <Box>
                <Label>Preview, run and read what it cost</Label>
                <CodeBlock code={node} />
              </Box>
            )}

            {tab === "ci" && (
              <Box>
                <Label>Block a pull request when the pass rate drops</Label>
                <CodeBlock code={ci} />
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                    mt: 1,
                  }}
                >
                  The run shows up here with its commit and pull request.
                  --fail-under exits 1 below the pass rate you set. Without
                  --budget, or with one over your admin&apos;s per-run limit,
                  the job fails before anything runs.
                </Typography>
              </Box>
            )}

            {tab === "train" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Drive episodes from your training loop</Label>
                  <CodeBlock code={pythonTrain(id)} />
                </Box>
                {trainPlan && (
                  <Box>
                    <Label>What plan.estimate prints</Label>
                    <CodeBlock code={trainPlan} />
                  </Box>
                )}
                <Box>
                  <Label>Or serve copies to a trainer on another machine</Label>
                  <CodeBlock code={serveCmd(id)} />
                </Box>
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                  }}
                >
                  The reward is this environment&apos;s evals. Works with
                  OpenEnv, TRL, verifiers and gym-style loops.
                </Typography>
              </Stack>
            )}

            {tab === "mcp" && (
              <Stack spacing={1.5}>
                <Box>
                  <Label>Ask your coding agent</Label>
                  <CodeBlock code={prompt} wrap />
                </Box>
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                  }}
                >
                  Runs, failures and fixes from your coding agent. Connect it
                  once in{" "}
                  <Link
                    component={RouterLink}
                    to={paths.dashboard.settings.mcpServer}
                    underline="always"
                    sx={{ color: "inherit" }}
                  >
                    Settings → MCP Server
                  </Link>
                  .
                </Typography>
              </Stack>
            )}
          </Stack>
        </Box>
      </Stack>
    </SideDrawer>
  );
}

RunFromCodeDrawer.propTypes = {
  budget: PropTypes.number,
  estimate: PropTypes.object,
  trainEvals: PropTypes.array,
  batchAt: PropTypes.number,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  agentName: PropTypes.string,
  repeats: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  parallel: PropTypes.number,
  /** "console" | "code" */
  managedIn: PropTypes.string,
  repo: PropTypes.string,
  /** Called when the fai env pull command is copied (marks it managed in code). */
  onPulled: PropTypes.func,
};
