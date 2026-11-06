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
import { QuietTag } from "../agents/AgentBits";
import LocalSteps from "../environments/LocalSteps";
import { CopyField } from "../components/primitives";

/* What `--dry-run` prints: the cases, versions and repeats that would run —
   nothing runs, and no cost estimate. */
/* Only `fi doctor` and `fi version` exist today; every other fi command is
   planned and says so. */
const isPlanned = (cmd) => /\bfi (?!doctor\b|version\b)/.test(String(cmd));
const BUILD_STEPS = [
  ["Start a build from your repo", "fi sim env create --repo ."],
  ["Answer its questions", "fi sim env chat --job <job-id>"],
  ["See what it changed", "fi sim env diff --job <job-id>"],
  ["Keep it", "fi sim env save --job <job-id>"],
  ["Or drop it", "fi sim env discard --job <job-id>"],
];
const NEEDS_INPUT = [
  "status: needs_input · job J-4821",
  "question: Who approves discounts over 10%?",
  "resume: fi sim env chat --job J-4821",
].join("\n");
const STALE_DRAFT = [
  "error: draft is stale",
  "current revision r7 (you had r5) · nothing was saved",
  "fi sim env diff --job J-4821 to see the changes",
].join("\n");
const mark = (steps) => steps.map((s) => ({ ...s, planned: isPlanned(s.cmd) }));
/*
  Run from code — the same run the Run simulation button starts, from the
  terminal, Python, CI or the developer's own agent (MCP). Every snippet
  carries this environment's ID and the repeats the header is set
  to, so what you copy runs exactly what you'd run here.

  Also says where the environment is managed: in the console (edits here make
  versions; `fi sim env pull` exports it to code) or in code (versions
  come from `fi sim env save`; console edits become a draft to merge).
*/

function Label({ children, planned = false }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
      <Typography sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500 }}>
        {children}
      </Typography>
      {planned && <QuietTag>Planned</QuietTag>}
    </Stack>
  );
}

Label.propTypes = { children: PropTypes.node, planned: PropTypes.bool };

const mono = { fontFamily: "ui-monospace, Menlo, monospace" };

/* The builder's terminal steps — a job you answer, review, then save or drop. */
function BuildFromTerminal() {
  return (
    <Box>
      <Label>Build it from the terminal</Label>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {BUILD_STEPS.map(([what, cmd]) => (
          <Stack
            key={cmd}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 1.5, py: 0.625 }}
          >
            <Typography
              sx={{
                fontSize: 12,
                lineHeight: "18px",
                width: 150,
                flexShrink: 0,
              }}
            >
              {what}
            </Typography>
            <Typography
              sx={{
                fontSize: 12,
                lineHeight: "18px",
                color: "text.secondary",
                flex: 1,
                minWidth: 0,
                ...mono,
              }}
            >
              {cmd}
            </Typography>
            <QuietTag>Planned</QuietTag>
          </Stack>
        ))}
      </Box>
      <Typography
        sx={{
          fontSize: 12,
          lineHeight: "16px",
          color: "text.tertiary",
          mt: 1,
          mb: 0.5,
        }}
      >
        Needs input
      </Typography>
      <CodeBlock code={NEEDS_INPUT} />
      <Typography
        sx={{
          fontSize: 12,
          lineHeight: "16px",
          color: "text.tertiary",
          mt: 1,
          mb: 0.5,
        }}
      >
        Stale draft
      </Typography>
      <CodeBlock code={STALE_DRAFT} />
      <Typography
        sx={{
          fontSize: 12,
          lineHeight: "16px",
          color: "text.tertiary",
          mt: 1,
        }}
      >
        Opening the job in the app shows the same question and draft.
      </Typography>
    </Box>
  );
}

const TABS = [
  { id: "cli", label: "CLI" },
  { id: "python", label: "Python" },
  { id: "node", label: "Node" },
  { id: "ci", label: "CI" },
  { id: "mcp", label: "Your agent (MCP)" },
  { id: "improve", label: "Improve" },
  { id: "package", label: "Environment release" },
];

/* The plan's exit codes — so a script reacts to the right problem. */
const EXIT_CODES = [
  ["0", "Success."],
  ["1", "Unexpected client error."],
  ["2", "Usage error — a bad flag or input; nothing was submitted."],
  ["3", "Not signed in, or not allowed."],
  ["4", "Not found."],
  ["5", "The run completed and failed the gate (--fail-under)."],
  [
    "6",
    "Network, server or infrastructure problem, or a required result is unavailable.",
  ],
];

export default function RunFromCodeDrawer({
  open,
  onClose,
  /* The tab it opens on — Environment release comes from the ⋯ menu. */
  initialTab = "cli",
  env,
  agentName,
  repeats = 3,
  parallel: _parallel = 20,
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
  const flags = `--agent-version <agent-version-id> --suite all --trials ${repeats}`;

  const cli = [
    {
      title: "Install and sign in",
      body: "Once per machine. Sign-in opens the browser; in CI use FI_API_KEY.",
      cmd: INSTALL_CMD,
    },
    {
      title: "Preview what runs",
      body: "Prints what would run — nothing runs.",
      cmd: `fi sim run --env ${id} ${flags} --dry-run`,
    },
    {
      title: "Run this environment",
      body: `Runs every scenario against ${agentName ? `the agent at ${agentName}` : "the agent version you pick"} — the same run as Run simulation${batchAt ? `; it's over 200 conversations, so ${batchAt} at once` : ""}. It prints the run's ID and its link here.`,
      hint: "Without --wait it returns once the run is submitted. Follow it with fi sim runs status <run-id> — closing the terminal doesn't stop the run; fi sim runs cancel <run-id> does. --suite picks a saved suite.",
      cmd: `fi sim run --env ${id} ${flags} --wait`,
    },
    {
      title: "Read the results",
      body: "Pass rate, the failing scenarios and why, with a link to the run here — the same run, by its ID.",
      hint: "fi sim runs logs <run-id> shows one conversation's events — nothing new runs.",
      cmd: "fi sim runs results <run-id>",
    },
    {
      title: "Fix it and rerun the same test",
      body: "The same environment, suite and repeats on your changed version — so the difference is the agent, not the test.",
      hint: "Comparing two runs from the terminal (fi experiments compare) comes later; compare them here.",
      cmd: `fi sim run --env ${id} --agent-version <new-agent-version-id> --suite all --trials ${repeats} --wait`,
    },
    {
      title: "Check what it cost",
      body: "The run's itemized credits, free-tier adjustment and pending items, as on its Cost tab.",
      hint: "usage show --group-by agent compares versions.",
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
      - run: ${INSTALL_CMD.split(" && ")[0]}
      # exit 5: the run completed and failed the gate · exit 6: infrastructure or result unavailable
      - run: |
          fi sim run --env ${id} --agent-version $AGENT_VERSION --suite must-pass --trials ${repeats} \\
            --wait --fail-under 0.9 --json --no-input > run.json
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}
          AGENT_VERSION: \${{ vars.AGENT_VERSION }}   # the version this PR builds`;

  /* A fix run from code — the same fix run the Improvements page shows, by
     the ID on its page. Flags follow the API's fields; the final fi spelling
     isn't set yet. */
  const improveSteps = [
    {
      title: "Start a fix run",
      body: "From a run's failures: the cases to fix, the ones it must not break, what it may change and what stays locked.",
      hint: "It stops at the credit budget or the time limit, whichever comes first. Surfaces it can't change yet are refused, not ignored.",
      cmd: `fi api fix-runs create --source-run-id <run-id> \\\n  --target-case-keys <case-keys> --protected-case-keys <case-keys> \\\n  --surfaces prompt,skill,config --locked-paths <paths> \\\n  --budget-credits 2000 --max-minutes 30`,
    },
    {
      title: "Follow it",
      body: "Its state, the candidates it tried and how each did on the held-back cases.",
      cmd: "fi api fix-runs get <fix-run-id> --json",
    },
    {
      title: "Act on the one you choose",
      body: "Open a pull request (a draft one under limited validation) or make it the environment default — nothing ships on its own.",
      cmd: "fi api fix-runs open-pr <fix-run-id> --candidate <candidate-id>",
    },
  ];

  /* Fix runs and releases from Python and Node — the same fix runs and
     releases by the same IDs; final signatures ship with the release. */
  const pythonMore = `# Improve: a fix run from a run's failures
fix = client.fix_runs.create(
    source_run_id="<run-id>",
    target_case_keys=[...], protected_case_keys=[...],
    surfaces=["prompt", "skill", "config"], locked_paths=[...],
    budget_credits=2000, max_minutes=30,
)
fix = client.fix_runs.wait(fix.id)        # state, candidates, held-back results
client.fix_runs.open_pr(fix.id, candidate=fix.candidates[0].id)

# Environment release: save a reviewed draft privately
release = client.sim.environments.save_release(environment="${id}", draft="<draft-id>")
release.wait_for_checks()                 # Ready once all five release checks pass`;

  const nodeMore = `// Improve: a fix run from a run's failures
let fix = await client.fixRuns.create({
  sourceRunId: "<run-id>",
  targetCaseKeys: [/* … */], protectedCaseKeys: [/* … */],
  surfaces: ["prompt", "skill", "config"], lockedPaths: [/* … */],
  budgetCredits: 2000, maxMinutes: 30
});
fix = await client.fixRuns.wait(fix.id);
await client.fixRuns.openPr(fix.id, { candidate: fix.candidates[0].id });

// Environment release
const release = await client.sim.environments.saveRelease({ environment: "${id}", draft: "<draft-id>" });
await release.waitForChecks();`;

  /* Save release (builder.md): a reviewed draft becomes a private release
     that has to pass the five release checks before it shows Ready. Using it
     with another agent is the normal environment flow. */
  const packageSteps = [
    {
      title: "Build or change it",
      body: "From your repo, or edit it here — either way you get a draft to review.",
      cmd: "fi sim env create --repo .",
    },
    {
      title: "Review the changes",
      body: "What the builder generated or changed, before anything is saved.",
      cmd: "fi sim env diff --job <job-id>",
    },
    {
      title: "Save release",
      body: "Saves a private release, visible to this workspace. It doesn't start a run or an improvement.",
      cmd: "fi sim env save --job <job-id>",
      after: (
        <Box sx={{ mt: 1 }}>
          <CodeBlock
            code={[
              "release v1.0 saved · private · checks pending",
              "  starts                     passed",
              "  resets                     passed",
              "  reference solution passes  passed",
              "  wrong answer fails         passed",
              "  hidden checks don't leak   passed",
              "Ready",
            ].join("\n")}
          />
        </Box>
      ),
    },
    {
      title: "Use it with another agent",
      body: "The normal environment flow: pick the agent version when you run. Each agent still needs its own readiness check.",
      cmd: `fi sim run --env ${id} --agent-version <agent-version-id> --wait`,
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
                    <Box sx={{ mb: 0.5 }}>
                      <QuietTag>Planned</QuietTag>
                    </Box>
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
                <LocalSteps steps={mark(cli)} />
                {/* For scripts and CI: never prompt, read the output, react
                    to the right exit code. */}
                <Box>
                  <Label planned>For scripts and CI</Label>
                  <CodeBlock
                    code={`fi sim run --env ${id} ${flags} --wait --json --no-input`}
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
                    --no-input never prompts — a missing choice is an error.
                    --json (or --output jsonl) prints results on stdout and
                    typed errors on stderr. Exit 6 isn&apos;t a reason to
                    resend: read the run, or retry with the same idempotency
                    key.
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
                <BuildFromTerminal />
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
                  Save a reviewed draft as a private release your workspace can
                  run with any agent. It shows Ready once its release checks
                  pass.
                </Typography>
                <LocalSteps steps={mark(packageSteps)} />
              </Stack>
            )}

            {tab === "python" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Preview, run and read the results</Label>
                  <CodeBlock code={python} />
                </Box>
                <Box>
                  <Label>Improve and save a release</Label>
                  <CodeBlock code={pythonMore} />
                </Box>
              </Stack>
            )}

            {tab === "node" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Preview, run and read the results</Label>
                  <CodeBlock code={node} />
                </Box>
                <Box>
                  <Label>Improve and save a release</Label>
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
                  The same fix run as on the Improvements page — copy its ID
                  there to follow or act on it from here.
                </Typography>
                <LocalSteps steps={mark(improveSteps)} />
              </Stack>
            )}

            {tab === "ci" && (
              <Box>
                <Label planned>Block a pull request on a failing gate</Label>
                <CodeBlock code={ci} />
                <Typography
                  sx={{
                    fontSize: 12,
                    lineHeight: "16px",
                    color: "text.tertiary",
                    mt: 1,
                  }}
                >
                  The run shows up here with its commit and pull request. It
                  exits 5 when the completed run is under 0.9 and 6 when it
                  couldn&apos;t finish or a result is missing — a partial run
                  never passes. Comparing against a baseline comes later.
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
