import React, { useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import PageHeader from "src/components/page-header";
import Section from "src/components/section";
import CodeBlock from "src/components/code-block";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  PYTHON_DEFINE,
  INSTALL_CMD,
  MCP_ADD_CMD,
  AGENT_DOCS_URL,
} from "src/sections/simulate-v2/environments/fromCode";
import useFiKeys from "src/sections/get-started/useFiKeys";
import KeyStrip from "src/sections/get-started/KeyStrip";
import ConfigPlanDrawer from "./ConfigPlanDrawer";
import { CONFIG_CMDS } from "./configPlan";
import { rlLoopCodeOf } from "src/sections/simulate-v2/_mock/release";

/*
  Developer — the reference for using Future AGI from code, for people who
  are already set up: your keys first (what CI and servers need), then
  install, CI, your coding agent, building from code, RL training and the
  docs for agents. Get started is the guided first run; this page links
  there for newcomers and never repeats its listening steps. Commands come
  from fromCode.js and the keys from useFiKeys, the same as Get started.
*/

function Note({ children }) {
  return (
    <Typography
      sx={{ fontSize: 12, lineHeight: "16px", color: "text.tertiary", mt: 1 }}
    >
      {children}
    </Typography>
  );
}

Note.propTypes = { children: PropTypes.node };

function GoButton({ to, href, children }) {
  return (
    <Button
      size="small"
      variant="outlined"
      endIcon={
        <Iconify
          icon={
            href ? "solar:arrow-right-up-linear" : "solar:arrow-right-linear"
          }
          width={14}
        />
      }
      {...(to
        ? { component: RouterLink, to }
        : { href, target: "_blank", rel: "noopener noreferrer" })}
    >
      {children}
    </Button>
  );
}

GoButton.propTypes = {
  to: PropTypes.string,
  href: PropTypes.string,
  children: PropTypes.node,
};

export default function DeveloperPage() {
  const [lang, setLang] = useState("python");
  const [planOpen, setPlanOpen] = useState(false);
  const keys = useFiKeys();

  return (
    <Box>
      <PageHeader
        title="Developer"
        description="Build and run environments, run evals and tracing, and reach RL endpoints, from your terminal, your code, CI or your own agent."
        actions={<GoButton href="https://docs.futureagi.com">Docs</GoButton>}
      />
      <Typography
        sx={{ fontSize: 13, lineHeight: "20px", color: "text.tertiary" }}
      >
        New here?{" "}
        <Link
          component={RouterLink}
          to={paths.dashboard.getstarted}
          underline="always"
          sx={{ color: "text.secondary" }}
        >
          Get started
        </Link>{" "}
        walks you through your first run.
      </Typography>

      <Stack spacing={4} sx={{ mt: 3 }}>
        <Section
          title="Keys"
          description="For CI and servers, where there's no browser to sign in. Every SDK and the CLI read them from the environment."
        >
          <KeyStrip keys={keys} />
          <Box sx={{ mt: 1.5 }}>
            <CodeBlock code={keys.envShown} copyText={keys.envCopy} />
          </Box>
          <Note>Shown masked; copy gives the full values.</Note>
        </Section>

        <Section
          title="Install and sign in"
          description="Once per machine. Sign-in opens the browser and saves a token for this workspace."
        >
          <ToggleButtonGroup
            exclusive
            size="small"
            value={lang}
            onChange={(_, v) => v && setLang(v)}
            sx={{ mb: 1.5 }}
          >
            <ToggleButton value="python">Python</ToggleButton>
            <ToggleButton value="typescript">TypeScript</ToggleButton>
          </ToggleButtonGroup>
          <CodeBlock code={INSTALL_CMD} />
          <Note>
            {lang === "python" ? (
              "Installs the fi CLI. Check it with fi doctor."
            ) : (
              <>
                The TypeScript SDK submits, watches, reads and stops runs — see
                an environment&apos;s Code button. To test a TypeScript agent,
                register it with fi agents push --repo . and run fi sim run
                --env &lt;environment-id&gt; --agent-version
                &lt;agent-version-id&gt;.
              </>
            )}
          </Note>
        </Section>

        <Section
          title="CI"
          description="Run an environment on every pull request and fail the check when the completed run is under your pass rate."
        >
          <CodeBlock
            code={`# .github/workflows/simulate.yml
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
          fi sim run --env <environment-id> --agent-version <agent-version-id> --suite must-pass \\
            --trials 3 --wait --fail-under 0.9 --json --no-input > run.json
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}
          FI_SECRET_KEY: \${{ secrets.FI_SECRET_KEY }}`}
          />
          <Note>
            Add the two keys above as repository secrets. Copy an
            environment&apos;s ID, or the whole command, from its Code button.
            Runs from CI show their commit and pull request on the run page. It
            exits 5 on a failed gate and 6 when the run couldn&apos;t finish — a
            partial run never passes. Comparing with a baseline comes later.
          </Note>
        </Section>

        <Section
          title="Your agent (MCP)"
          description="Let Claude Code, Cursor, VS Code or Windsurf run simulations, read failures and add scenarios for you."
          actions={
            <GoButton to={paths.dashboard.settings.mcpServer}>
              Set up MCP server
            </GoButton>
          }
        >
          <CodeBlock code={MCP_ADD_CMD} />
          <Note>
            Claude Code shown. Other clients and which tools your agent may use
            are on the MCP server page.
          </Note>
        </Section>

        <Section
          title="Build environments from code"
          description="Define an environment next to your agent and run it from code. The console builder works too; both make the same environments."
          actions={
            <GoButton to={paths.dashboard.simulate.environments}>
              Environments
            </GoButton>
          }
        >
          <CodeBlock code={PYTHON_DEFINE} />
          <Note>
            In the console: Environments → Build environment → Build from code,
            for the same steps with the CLI or your coding agent.
          </Note>
        </Section>

        {/* rl.md: RL endpoints and their docs, not a Train button. */}
        <Section
          title="RL endpoints"
          description="Train your own model on a locked environment: many sessions at once, each in its own world, rewarded by the environment's checks."
        >
          <CodeBlock
            code={rlLoopCodeOf(
              { id: "<environment-id>" },
              { lockedRelease: { version: "v1" } },
            )}
          />
          <Note>
            Open RL access on a locked environment&apos;s Overview for its key
            and the full guide. Sessions use the same credits as simulations.
          </Note>
        </Section>

        {/* T38: workspace settings as code — profiles name workspaces. */}
        <Section
          title="Workspace settings as code"
          description="Keep this workspace's evals, prompts, alerts and datasets in a file. Plan shows the diff, apply makes the reviewed changes, promote copies them to another workspace."
          actions={
            <Button
              size="small"
              variant="outlined"
              onClick={() => setPlanOpen(true)}
              startIcon={
                <Iconify icon="solar:document-text-linear" width={14} />
              }
            >
              Review a plan
            </Button>
          }
        >
          <CodeBlock code={CONFIG_CMDS} />
          <Note>
            A profile names a workspace (staging, production), not a simulation
            environment. Every change says how far it can be rolled back. It
            never deploys or changes your agent.
          </Note>
          <ConfigPlanDrawer
            open={planOpen}
            onClose={() => setPlanOpen(false)}
          />
        </Section>

        <Section
          title="Docs for agents"
          description="Point your coding agent at our docs so it writes Future AGI code correctly."
        >
          <CodeBlock code={AGENT_DOCS_URL} />
          <Note>
            A plain-text index of every docs page, in the llms.txt format.{" "}
            <Link
              href={AGENT_DOCS_URL}
              target="_blank"
              rel="noopener noreferrer"
              underline="always"
              sx={{ color: "inherit" }}
            >
              Open it
            </Link>
          </Note>
        </Section>
      </Stack>
    </Box>
  );
}
