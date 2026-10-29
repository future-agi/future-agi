import React, { useEffect, useState } from "react";
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
import { trainCmd, serveCmd, INSTALL_CMD } from "../environments/fromCode";
import SideDrawer from "../components/SideDrawer";
import LocalSteps from "../environments/LocalSteps";
import { CopyField } from "../components/primitives";
import {
  RATE_PLAN,
  WORKSPACE_POLICY,
  estimatePackageValidation,
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
  versions; `agent-learn env pull` exports it to code) or in code (versions
  come from `agent-learn env save`; console edits become a draft to merge).
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
  { id: "improve", label: "Improve" },
  { id: "train", label: "Train (RL)" },
  { id: "package", label: "Package" },
];

/* Where a run happens (--mode) — what each one means for the person's data
   and their terminal. */
const MODES = [
  {
    id: "hosted",
    label: "Hosted",
    line: "Your agent and this world run here. Close the terminal and it keeps going.",
  },
  {
    id: "hybrid",
    label: "Hybrid",
    line: "Your agent runs on your machine and talks to this world here — only its messages and tool calls cross. Keep the terminal open.",
  },
  {
    id: "local",
    label: "Local",
    line: "Your agent and the world both run on your machine; results stay there until you export them.",
  },
];

/* What each exit code means — so a script reacts to the right problem. */
const EXIT_CODES = [
  ["0", "Done. A detached run was accepted."],
  ["2", "Bad input or an empty selection — nothing was submitted."],
  ["3", "Sign-in or permission problem."],
  ["4", "Budget or usage limit — nothing new started."],
  ["5", "Infrastructure or provider failure, not your agent."],
  ["6", "A real regression (with --fail-on-regression)."],
  ["7", "Evidence missing, or the runs can't be compared."],
  ["130", "You stopped a local run. A hosted run keeps going."],
];

export default function RunFromCodeDrawer({
  open,
  onClose,
  /* The tab it opens on — Package and Train come from the ⋯ menu. */
  initialTab = "cli",
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
  const [tab, setTab] = useState(initialTab);
  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);
  const [mode, setMode] = useState("hosted");
  const local = mode !== "hosted";
  const id = env?.id || "env_…";
  const agentFlag = local ? " --agent my-agent" : "";
  const flags = `--mode ${mode}${agentFlag} --attempts ${repeats} --concurrency ${batchAt || parallel}`;
  const cap = `--budget ${budget} --budget-unit credits`;

  const cli = [
    {
      title: "Install and sign in",
      body: "Once per machine. Sign-in opens the browser; in CI use FI_API_KEY.",
      cmd: INSTALL_CMD,
    },
    ...(local
      ? [
          {
            title: "Register your agent",
            body: "The agent on your machine — how to start it. The test checks it answers; it isn't a pass.",
            hint: "--type http --url … for an agent behind a URL; --mapping for another input/output shape.",
            cmd: "agent-learn agent add my-agent --type process --command python --arg agent.py --workdir .\nagent-learn agent test my-agent",
          },
        ]
      : []),
    {
      title: "Preview the cost",
      body: "The same scope, estimate and limit check as the run plan — nothing runs.",
      cmd: `agent-learn run --env ${id} ${flags} ${cap} --dry-run`,
      after: estimate && (
        <Box sx={{ mt: 1 }}>
          <CodeBlock code={dryRunOutput(estimate, budget, repeats)} />
        </Box>
      ),
    },
    {
      title: "Run this environment",
      body: `Runs every scenario against ${agentName ? `the agent at ${agentName}` : "its agent"} — the same run as Run simulation${batchAt ? `; it's over 200 conversations, so ${batchAt} at once` : ""}. No new calls start past --budget. It prints the run's ID and its link here.`,
      hint:
        mode === "hosted"
          ? "--detach gives you the terminal back; the run keeps going. Follow it with agent-learn status <run-id> --watch — Ctrl-C stops watching, not the run. --scenario or --group picks a subset."
          : "Keep the terminal open — your agent is part of the run. Ctrl-C stops it and says where the partial results are (exit 130). --scenario or --group picks a subset.",
      cmd: `agent-learn run --env ${id} ${flags} ${cap}`,
    },
    {
      title: "Read the results",
      body: "Pass rate, the failing scenarios and why, with a link to the run here — the same run, by its ID.",
      hint: "agent-learn logs <run-id> --trial <trial-id> shows one conversation's events — nothing new runs.",
      cmd: "agent-learn results <run-id> --failed-only --include checks,usage,trace",
    },
    {
      title: "Fix it and rerun the same test",
      body: "The same scenarios and settings, on your changed agent — so the difference is the agent, not the test.",
      hint: "--failed-only reruns just what failed: quick for checking a fix, but not a full comparison. Then agent-learn compare <run-id> <new-run-id> --scope identical.",
      cmd: local
        ? 'agent-learn agent version my-agent --message "Fix the failing case"\nagent-learn rerun <run-id> --agent my-agent@2'
        : "agent-learn rerun <run-id> --agent <agent>@<new-version>",
    },
    {
      title: "Check what it cost",
      body: "The run's itemized credits, free-tier adjustment and what's pending on your own accounts — the same lines as its Cost tab.",
      hint: `usage show --group-by agent compares versions; admins set limits with usage budget set. A --budget over your admin's ${fmtCredits(WORKSPACE_POLICY.maxRunCredits)} per run is refused here too.`,
      cmd: "agent-learn usage show --run <run-id>",
    },
  ];

  /* The doc's call sequence — preview, create, wait — on an authenticated
     client; the final method signatures ship with the release. */
  const python = `# client: an authenticated SDK client for this project

plan = client.runs.preview(
    environment="${id}",
    mode="${mode}",${local ? `\n    agent="my-agent",` : ""}
    attempts=${repeats},
    budget={"amount": ${budget}, "unit": "credits"},
)
print(plan.trial_count, plan.cost_estimate)   # same estimate as the run plan

run = client.runs.create(
    plan_id=plan.id,
    idempotency_key="ci-candidate-commit",    # a retry returns this same run
)
result = client.runs.wait(run.id)             # waiting never starts another run
print(result.status, result.failed_cases, result.usage)
print(run.ui_url)                             # the same run, here`;

  const pythonOwnAgent = `agent-learn agent add my-agent --type process --command python --arg agent.py --workdir .
agent-learn agent test my-agent
agent-learn run --env ${id} --agent my-agent --mode hybrid --attempts ${repeats} ${cap}`;

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

  const node = `// client: an authenticated SDK client for this project

const plan = await client.runs.preview({
  environment: "${id}",
  mode: "${mode}",${local ? `\n  agent: "my-agent",` : ""}
  attempts: ${repeats},
  budget: { amount: ${budget}, unit: "credits" }
});
console.log(plan.trialCount, plan.costEstimate);  // same estimate as the run plan

const run = await client.runs.create({
  planId: plan.id,
  idempotencyKey: "ci-candidate-commit"           // a retry returns this same run
});
const result = await client.runs.wait(run.id);
console.log(result.status, result.failedCases, result.usage);`;

  const ci = `# .github/workflows/simulate.yml
name: Simulate
on: [pull_request]
jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install agent-learning-kit
      # CI needs a limit: a run without one fails before it starts.
      - run: |
          agent-learn run --env ${id} --mode hosted --group must-pass ${cap} --non-interactive --yes --output json > run.json
          agent-learn compare $BASELINE_RUN $(jq -r .run_id run.json) --scope identical --fail-on-regression
          agent-learn export $(jq -r .run_id run.json) --format junit --destination ./results.xml
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}
          BASELINE_RUN: \${{ vars.BASELINE_RUN }}   # e.g. main's last run`;

  /* An improvement experiment from code — the same experiment the
     Improvements page shows, by the ID on its page. */
  const improveSteps = [
    {
      title: "Create the experiment",
      body: "From a run's failures, with what it may change and the scenarios it's checked on. Nothing runs yet.",
      hint: "--suggest-only diagnoses and suggests a change without trials. Surfaces it can't change yet are refused, not ignored.",
      cmd: `agent-learn improve create --baseline <run-id> --goal "Fix the failing cases" \\\n  --allow prompt,skill,config --suite ${id}/target --holdout ${id}/holdout \\\n  --budget 2000 --budget-unit credits --max-candidates 3`,
    },
    {
      title: "Run it",
      body: "Tries the candidates on the target scenarios and checks the finalists on held-out ones.",
      hint: "--candidate picks some; a higher cap needs a new review.",
      cmd: "agent-learn improve run <experiment-id>",
    },
    {
      title: "Read what it found",
      body: "Each candidate's evidence, the held-out result, trade-offs and a recommendation — nothing ships on its own.",
      cmd: "agent-learn improve results <experiment-id>",
    },
    {
      title: "Promote the one you choose",
      body: "As a new agent version, a pull request, or an exported patch.",
      hint: "--target patch-pr opens a PR; --target export --destination ./patch writes it to a file.",
      cmd: "agent-learn improve promote <experiment-id> --candidate <candidate-id> --target agent-version",
    },
  ];

  /* Improve, train and package from Python and Node — the same experiments
     and jobs by the same IDs; final signatures ship with the release. */
  const pythonMore = `# Improve: an experiment from a run's failures
exp = client.improvements.create(
    baseline="<run-id>", goal="Fix the failing cases",
    allow=["prompt", "skill", "config"],
    suite="${id}/target", holdout="${id}/holdout",
    budget={"amount": 2000, "unit": "credits"},
)
client.improvements.run(exp.id)
found = client.improvements.wait(exp.id)      # candidates, held-out, recommendation
client.improvements.promote(exp.id, candidate=found.recommended, target="agent-version")

# Train: the TRL path, checked on held-out cases
job = client.training.create(
    environment="${id}", trainer="trl", base_model="<base-model>", mode="rollout",
    train_split="${id}/train", dev_split="${id}/dev", holdout="${id}/holdout",
    reward="${id}/reward", budget={"amount": 5000, "unit": "credits"},
)
client.training.start(job.id, idempotency_key="train-${id}-1")  # a retry returns this job
client.training.cancel(job.id)                # or pause / resume, where supported
print(client.training.results(job.id))        # trained model vs the original

# Package: check and publish a private world
check = client.worlds.validate("./my-world", budget={"amount": 100, "unit": "credits"})
client.worlds.publish("./my-world", visibility="workspace", version="1.0.0", validation=check.id)`;

  const nodeMore = `// Improve
const exp = await client.improvements.create({
  baseline: "<run-id>", goal: "Fix the failing cases",
  allow: ["prompt", "skill", "config"],
  suite: "${id}/target", holdout: "${id}/holdout",
  budget: { amount: 2000, unit: "credits" }
});
await client.improvements.run(exp.id);
const found = await client.improvements.wait(exp.id);
await client.improvements.promote(exp.id, { candidate: found.recommended, target: "agent-version" });

// Train
const job = await client.training.create({
  environment: "${id}", trainer: "trl", baseModel: "<base-model>", mode: "rollout",
  trainSplit: "${id}/train", devSplit: "${id}/dev", holdout: "${id}/holdout",
  reward: "${id}/reward", budget: { amount: 5000, unit: "credits" }
});
await client.training.start(job.id, { idempotencyKey: "train-${id}-1" });
console.log(await client.training.results(job.id));

// Package
const check = await client.worlds.validate("./my-world", { budget: { amount: 100, unit: "credits" } });
await client.worlds.publish("./my-world", { visibility: "workspace", version: "1.0.0", validation: check.id });`;

  /* A private world package: start one, preview and check it, publish it
     to the workspace, and adopt it — the same steps whoever runs them. */
  const packageSteps = [
    {
      title: "Start a world package",
      body: "A starter for a game, an app, a workflow or an empty world — edit it next to your code.",
      cmd: "agent-learn world init my-world --template app",
    },
    {
      title: "Try it in a sandbox",
      body: "Open it with a starting state and look around before checking it.",
      cmd: `agent-learn world preview ./my-world --mode ${mode === "local" ? "local" : "hosted"} --budget 50`,
    },
    {
      title: "Check it",
      body: "Starts, resets, a reference run passes, deliberate mistakes fail, and episodes stay apart. Publishing needs every check.",
      cmd: "agent-learn world validate ./my-world --budget 100",
      after: (
        <Box sx={{ mt: 1 }}>
          <CodeBlock
            code={(() => {
              const v = estimatePackageValidation();
              return [
                ...v.parts.map(
                  (p) =>
                    `  ${p.label.padEnd(30)}${p.credits ? `${Math.round(p.credits)} credits` : "measured, not billed"}   ${p.detail}`,
                ),
                `Estimate  ≈ ${Math.round(v.total)} credits`,
                "Limit     100 credits — checks past it don't start; the ones done are kept",
                `Rates     ${RATE_PLAN.name} · ${RATE_PLAN.version}`,
              ].join("\n");
            })()}
          />
        </Box>
      ),
    },
    {
      title: "Publish it privately",
      body: "An unchangeable version, visible to you or your workspace.",
      cmd: "agent-learn world publish ./my-world --visibility workspace --version 1.0.0 --notes ./CHANGES.md --validation <validation-id>",
    },
    {
      title: "Build environments from it",
      body: "You or a teammate adopt the released world for an agent, then run it like any environment.",
      cmd: "agent-learn env adopt my-world@1.0.0 --agent <agent> --name my-world-tests",
    },
  ];

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
                        agent-learn env save
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
                    <CopyField
                      value={`agent-learn env pull ${id}`}
                      onCopy={onPulled}
                    />
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

            {/* Where it runs — every command and call below follows it. */}
            {["cli", "python", "node"].includes(tab) && (
              <Box>
                <Label>Where it runs</Label>
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={mode}
                  onChange={(_, v) => v && setMode(v)}
                  aria-label="Where it runs"
                >
                  {MODES.map((m) => (
                    <ToggleButton key={m.id} value={m.id}>
                      {m.label}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                    mt: 0.75,
                  }}
                >
                  {MODES.find((m) => m.id === mode)?.line}
                </Typography>
              </Box>
            )}

            {tab === "cli" && (
              <Stack spacing={2.5}>
                <LocalSteps steps={cli} />
                {/* For scripts and CI: never prompt, read the output, react
                    to the right exit code. */}
                <Box>
                  <Label>For scripts and CI</Label>
                  <CodeBlock
                    code={`agent-learn run --env ${id} ${flags} ${cap} --non-interactive --yes --output json`}
                  />
                  <Typography
                    sx={{
                      fontSize: 12,
                      lineHeight: "16px",
                      color: "text.tertiary",
                      mt: 1,
                      mb: 1,
                    }}
                  >
                    --non-interactive never prompts — a missing choice or budget
                    is an error. --yes accepts the plan shown; it never allows
                    live tools or a bigger budget. --output json prints the run
                    for your script.
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      "& > * + *": {
                        borderTop: "1px solid",
                        borderColor: "divider",
                      },
                    }}
                  >
                    {EXIT_CODES.map(([code, meaning]) => (
                      <Stack
                        key={code}
                        direction="row"
                        spacing={1.5}
                        sx={{ px: 1.5, py: 0.625 }}
                      >
                        <Typography
                          sx={{
                            fontSize: 12,
                            lineHeight: "18px",
                            fontFamily: "monospace",
                            width: 32,
                            flexShrink: 0,
                          }}
                        >
                          {code}
                        </Typography>
                        <Typography
                          sx={{
                            fontSize: 12,
                            lineHeight: "18px",
                            color: "text.secondary",
                          }}
                        >
                          {meaning}
                        </Typography>
                      </Stack>
                    ))}
                  </Box>
                </Box>
              </Stack>
            )}

            {tab === "package" && (
              <Stack spacing={1.5}>
                <Typography
                  sx={{
                    fontSize: 13,
                    lineHeight: "20px",
                    color: "text.secondary",
                  }}
                >
                  Package your own world — its tools, data and rules — check it,
                  and publish it privately so your team can build environments
                  from it. Public publishing isn&apos;t available.
                </Typography>
                <LocalSteps steps={packageSteps} />
              </Stack>
            )}

            {tab === "python" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Preview, run and read the results</Label>
                  <CodeBlock code={python} />
                </Box>
                <Box>
                  <Label>Or test your local agent, without deploying it</Label>
                  <CodeBlock code={pythonOwnAgent} />
                </Box>
                <Box>
                  <Label>Improve, train and package</Label>
                  <CodeBlock code={pythonMore} />
                </Box>
              </Stack>
            )}

            {tab === "node" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Preview, run and read what it cost</Label>
                  <CodeBlock code={node} />
                </Box>
                <Box>
                  <Label>Improve, train and package</Label>
                  <CodeBlock code={nodeMore} />
                </Box>
              </Stack>
            )}

            {tab === "improve" && (
              <Stack spacing={1.5}>
                <Typography
                  sx={{
                    fontSize: 13,
                    lineHeight: "20px",
                    color: "text.secondary",
                  }}
                >
                  The same experiment as on the Improvements page — copy its ID
                  there to read or promote it from here.
                </Typography>
                <LocalSteps steps={improveSteps} />
              </Stack>
            )}

            {tab === "ci" && (
              <Box>
                <Label>Block a pull request on a regression</Label>
                <CodeBlock code={ci} />
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                    mt: 1,
                  }}
                >
                  The run shows up here with its commit and pull request. The
                  comparison runs the identical scenarios as the baseline: it
                  exits 6 on a real regression and 7 when the two runs
                  can&apos;t be compared — never a silent pass. Without
                  --budget, or with one over your admin&apos;s per-run limit,
                  the job fails before anything runs (exit 4).
                </Typography>
              </Box>
            )}

            {tab === "train" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Train a model on this environment</Label>
                  <CodeBlock code={trainCmd(id)} />
                </Box>
                {trainPlan && (
                  <Box>
                    <Label>What train create shows before it starts</Label>
                    <CodeBlock code={trainPlan} />
                  </Box>
                )}
                <Box>
                  <Label>Or pull it for a trainer on another machine</Label>
                  <CodeBlock code={serveCmd(id)} />
                </Box>
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                  }}
                >
                  The reward is this environment&apos;s evals. The first trainer
                  is TRL. Training, development and held-out cases stay apart,
                  and the trained model is checked on held-out cases it never
                  saw — no improvement is a valid result. A create missing a
                  split, reward or resources, or a resume the trainer can&apos;t
                  do, stops with exit 2 and says what&apos;s missing — nothing
                  starts and nothing is charged.
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
  initialTab: PropTypes.string,
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
  /** Called when the agent-learn env pull command is copied (marks it managed in code). */
  onPulled: PropTypes.func,
};
