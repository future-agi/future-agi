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
      title: "Run this environment",
      body: `Runs every scenario against ${agentName ? `the agent at ${agentName}` : "its agent"} — the same run as Run simulation${batchAt ? `; it's over 200 conversations, so ${batchAt} at once` : ""}.`,
      hint: "--scenarios picks a subset; --agent-url tests a local build.",
      cmd: `fai sim run ${id} ${flags}`,
    },
    {
      title: "Read the results",
      body: "Pass rate, the failing scenarios and why, with a link to the run here.",
      cmd: "fai sim results --latest --failures",
    },
  ];

  const python = `from fi.simulate import Environment

env = Environment.get("${id}")

run = env.run(repeats=${repeats}, parallel=${parallel})
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

  const ci = `# .github/workflows/simulate.yml
name: Simulate
on: [pull_request]
jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install futureagi
      - run: fai sim run ${id} ${flags} --fail-under 0.9
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
                  --fail-under exits 1 below the pass rate you set.
                </Typography>
              </Box>
            )}

            {tab === "train" && (
              <Stack spacing={2}>
                <Box>
                  <Label>Drive episodes from your training loop</Label>
                  <CodeBlock code={pythonTrain(id)} />
                </Box>
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
