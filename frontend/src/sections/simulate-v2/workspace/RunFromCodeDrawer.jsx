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
import { INSTALL_CMD } from "../environments/fromCode";
import SideDrawer from "../components/SideDrawer";
import LocalSteps from "../environments/LocalSteps";
import { CopyField } from "../components/primitives";
import {
  RATE_PLAN,
  WORKSPACE_POLICY,
  estimatePackageValidation,
  fmtCredits,
} from "../_mock/pricing";

/* What `--dry-run` prints: the run plan's scope, estimate and limit, in the
   same units and rates — nothing runs. */
/*
  Run from code — the same run the Run simulation button starts, from the
  terminal, Python, CI or the developer's own agent (MCP). Every snippet
  carries this environment's ID and the repeats/parallel the header is set
  to, so what you copy runs exactly what you'd run here.

  Also says where the environment is managed: in the console (edits here make
  versions; `fi sim env pull` exports it to code) or in code (versions
  come from `fi sim env save`; console edits become a draft to merge).
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
  { id: "package", label: "Environment release" },
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
  ["130", "You stopped watching. The run keeps going in the cloud."],
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
  budget: _budget = 1000,
  /* The run plan's estimate for this scope (estimateRunPlan) — what
     --dry-run prints, so the two read the same. */
  estimate: _estimate = null,
  /* The environment's evals, resolved — the held-out evaluation's judges. */
}) {
  const [tab, setTab] = useState(initialTab);
  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);
  /* Platform runs are cloud-only: the terminal starts and watches them. */
  const id = env?.id || "env_…";
  const flags = `--agent-version <agent>@<version> --suite all --trials ${repeats} --concurrency ${batchAt || parallel}`;

  const cli = [
    {
      title: "Install and sign in",
      body: "Once per machine. Sign-in opens the browser; in CI use FI_API_KEY.",
      cmd: INSTALL_CMD,
    },
    {
      title: "Preview what runs",
      body: "The exact cases, versions and repeats the run would use — nothing runs.",
      cmd: `fi sim run --env ${id} ${flags} --dry-run`,
    },
    {
      title: "Run this environment",
      body: `Runs every scenario against ${agentName ? `the agent at ${agentName}` : "its agent"} — the same run as Run simulation${batchAt ? `; it's over 200 conversations, so ${batchAt} at once` : ""}. It prints the run's ID and its link here.`,
      hint: "--detach gives you the terminal back; the run keeps going. Follow it with fi sim runs status <run-id> --watch — Ctrl-C stops watching, not the run. --scenario or --suite picks a subset.",
      cmd: `fi sim run --env ${id} ${flags}`,
    },
    {
      title: "Read the results",
      body: "Pass rate, the failing scenarios and why, with a link to the run here — the same run, by its ID.",
      hint: "fi sim runs logs <run-id> --trial <trial-id> shows one conversation's events — nothing new runs.",
      cmd: "fi sim runs results <run-id> --failed-only --include checks,usage,trace",
    },
    {
      title: "Fix it and rerun the same test",
      body: "The same scenarios and settings, on your changed agent — so the difference is the agent, not the test.",
      hint: "--failed-only reruns just what failed: quick for checking a fix, but not a full comparison. Then fi experiments compare <run-id> <new-run-id> --scope identical.",
      cmd: "fi sim run --from-run <run-id> --agent-version <agent>@<new-version>",
    },
    {
      title: "Check what it cost",
      body: "The run's itemized credits, free-tier adjustment and what's pending on your own accounts — the same lines as its Cost tab.",
      hint: `usage show --group-by agent compares versions; admins set limits with usage budget set. A --budget over your admin's ${fmtCredits(WORKSPACE_POLICY.maxRunCredits)} per run is refused here too.`,
      cmd: "fi api usage show --run <run-id>",
    },
  ];

  /* The doc's call sequence — preview, create, wait — on an authenticated
     client; the final method signatures ship with the release. */
  const python = `# client: an authenticated SDK client for this project

plan = client.sim.runs.preview(
    environment="${id}",
    trials=${repeats},
)
print(plan.trial_count)                        # the exact cases that would run

run = client.sim.runs.create(
    plan_id=plan.id,
    idempotency_key="ci-candidate-commit",    # a retry returns this same run
)
result = client.sim.runs.wait(run.id)             # waiting never starts another run
print(result.status, result.failed_cases, result.usage)
print(run.ui_url)                             # the same run, here`;

  const node = `// client: an authenticated SDK client for this project

const plan = await client.sim.runs.preview({
  environment: "${id}",
  trials: ${repeats}
});
console.log(plan.trialCount);  // the exact cases that would run

const run = await client.sim.runs.create({
  planId: plan.id,
  idempotencyKey: "ci-candidate-commit"           // a retry returns this same run
});
const result = await client.sim.runs.wait(run.id);
console.log(result.status, result.failedCases, result.usage);`;

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
      - run: |
          fi sim run --env ${id} --suite must-pass --wait --fail-under 0.9 --non-interactive --yes --output json > run.json
          fi experiments compare $BASELINE_RUN $(jq -r .run_id run.json) --scope identical --fail-on-regression
          fi sim runs export $(jq -r .run_id run.json) --format junit --destination ./results.xml
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
      cmd: `fi api fix-runs create --baseline <run-id> --goal "Fix the failing cases" \\\n  --allow prompt,skill,config --suite ${id}/target --holdout ${id}/holdout \\\n  --budget 2000 --budget-unit credits --max-candidates 3`,
    },
    {
      title: "Run it",
      body: "Tries the candidates on the target scenarios and checks the finalists on held-out ones.",
      hint: "--candidate picks some; a higher cap needs a new review.",
      cmd: "fi api fix-runs run <experiment-id>",
    },
    {
      title: "Read what it found",
      body: "Each candidate's evidence, the held-out result, trade-offs and a recommendation — nothing ships on its own.",
      cmd: "fi api fix-runs results <experiment-id>",
    },
    {
      title: "Make the one you choose active",
      body: "As a new agent version, a pull request, or an exported patch.",
      hint: "--target patch-pr opens a PR; --target export --destination ./patch writes it to a file.",
      cmd: "fi api fix-runs promote <experiment-id> --candidate <candidate-id> --target agent-version",
    },
  ];

  /* Improve and package from Python and Node — the same experiments
     and jobs by the same IDs; final signatures ship with the release. */
  const pythonMore = `# Improve: an experiment from a run's failures
exp = client.fix_runs.create(
    baseline="<run-id>", goal="Fix the failing cases",
    allow=["prompt", "skill", "config"],
    suite="${id}/target", holdout="${id}/holdout",
    budget={"amount": 2000, "unit": "credits"},
)
client.fix_runs.run(exp.id)
found = client.fix_runs.wait(exp.id)      # candidates, held-out, recommendation
client.fix_runs.promote(exp.id, candidate=found.recommended, target="agent-version")

# Environment release: check it and save it privately
check = client.sim.environments.validate("./my-world", budget={"amount": 100, "unit": "credits"})
client.sim.environments.save_release("./my-world", visibility="workspace", version="1.0.0", validation=check.id)`;

  const nodeMore = `// Improve
const exp = await client.fix_runs.create({
  baseline: "<run-id>", goal: "Fix the failing cases",
  allow: ["prompt", "skill", "config"],
  suite: "${id}/target", holdout: "${id}/holdout",
  budget: { amount: 2000, unit: "credits" }
});
await client.fix_runs.run(exp.id);
const found = await client.fix_runs.wait(exp.id);
await client.fix_runs.promote(exp.id, { candidate: found.recommended, target: "agent-version" });

// Package
const check = await client.sim.environments.validate("./my-world", { budget: { amount: 100, unit: "credits" } });
await client.sim.environments.save_release("./my-world", { visibility: "workspace", version: "1.0.0", validation: check.id });`;

  /* A private environment release: start one, preview and check it, save
     it to the workspace, and adopt it — the same steps whoever runs them. */
  const packageSteps = [
    {
      title: "Start an environment",
      body: "A starter for a game, an app, a workflow or an empty world — edit it next to your code.",
      cmd: "fi sim env init my-world --template app",
    },
    {
      title: "Try it in a sandbox",
      body: "Open it with a starting state and look around before checking it.",
      cmd: `fi sim env preview ./my-world --budget 50`,
    },
    {
      title: "Check it",
      body: "Starts, resets, a reference run passes, deliberate mistakes fail, and episodes stay apart. Publishing needs every check.",
      cmd: "fi sim env validate ./my-world --budget 100",
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
      cmd: "fi sim env publish ./my-world --visibility workspace --version 1.0.0 --notes ./CHANGES.md --validation <validation-id>",
    },
    {
      title: "Build environments from it",
      body: "You or a teammate adopt the released world for an agent, then run it like any environment.",
      cmd: "fi sim env adopt my-world@1.0.0 --agent <agent> --name my-world-tests",
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
                        fi sim env save
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
                      value={`fi sim env pull ${id}`}
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

            {tab === "cli" && (
              <Stack spacing={2.5}>
                <LocalSteps steps={cli} />
                {/* For scripts and CI: never prompt, read the output, react
                    to the right exit code. */}
                <Box>
                  <Label>For scripts and CI</Label>
                  <CodeBlock
                    code={`fi sim run --env ${id} ${flags} --non-interactive --yes --output json`}
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
                  Release your own world — its tools, data and rules — check it,
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
                  <Label>Improve and package</Label>
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
                  <Label>Improve and package</Label>
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
                  can&apos;t be compared — never a silent pass.
                </Typography>
              </Box>
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
  /** Called when the fi sim env pull command is copied (marks it managed in code). */
  onPulled: PropTypes.func,
};
