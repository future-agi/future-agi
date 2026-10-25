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
  pythonTrain,
  serveCmd,
} from "src/sections/simulate-v2/environments/fromCode";

/*
  Developer — everything to use Future AGI from code, in one place: install
  and sign in, keys as environment variables, CI, your own agent over MCP,
  and the docs written for agents. Keys and the MCP server keep their own
  pages; this page points to them rather than repeating them.
*/

const MCP_URL = "https://api.futureagi.com/mcp";

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

  return (
    <Box>
      <PageHeader
        title="Developer"
        description="Build, run and train environments, and run evals and tracing, from your terminal, your code, CI or your own agent."
        actions={<GoButton href="https://docs.futureagi.com">Docs</GoButton>}
      />

      <Stack spacing={4} sx={{ mt: 1 }}>
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
          {lang === "python" ? (
            <>
              <CodeBlock code={"pip install futureagi\nfai login"} />
              <Note>
                Installs the Python SDK and the fai CLI. Check it with fai
                whoami.
              </Note>
            </>
          ) : (
            <>
              <CodeBlock code={"pip install futureagi\nfai login"} />
              <Note>
                The TypeScript SDK is coming soon. Until then, test a TypeScript
                agent with the fai CLI pointed at its URL: fai sim run
                &lt;environment&gt; --agent-url http://localhost:3000/chat
              </Note>
            </>
          )}
        </Section>

        <Section
          title="Build environments from code"
          description="Define an environment next to your agent, run it, and push it when you want it here. The console builder works too; both make the same environments."
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

        <Section
          title="Train (RL)"
          description="Every environment is also an RL environment: reset, step, and a reward from its verifiers — the same evals that grade a run."
        >
          <CodeBlock code={pythonTrain()} />
          <Box sx={{ mt: 1.5 }}>
            <CodeBlock code={serveCmd()} />
          </Box>
          <Note>
            fai env serve runs copies for a trainer on another machine. Works
            with OpenEnv, TRL, verifiers and gym-style loops.
          </Note>
        </Section>

        <Section
          title="Keys"
          description="For CI and servers, where there's no browser to sign in. Every SDK and the CLI read them from the environment."
          actions={<GoButton to={paths.dashboard.keys}>Manage keys</GoButton>}
        >
          <CodeBlock code={'export FI_API_KEY="…"\nexport FI_SECRET_KEY="…"'} />
        </Section>

        <Section
          title="CI"
          description="Run an environment on every pull request and fail the check when the pass rate drops."
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
      - run: pip install futureagi
      - run: fai sim run <environment-id> --repeats 3 --fail-under 0.9
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}
          FI_SECRET_KEY: \${{ secrets.FI_SECRET_KEY }}`}
          />
          <Note>
            Copy an environment&apos;s ID, or the whole command, from its Code
            button. Runs from CI show their commit and pull request on the run
            page.
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
          <CodeBlock
            code={`claude mcp add futureagi --transport http ${MCP_URL}`}
          />
          <Note>
            Claude Code shown. Other clients and which tools your agent may use
            are on the MCP server page.
          </Note>
        </Section>

        <Section
          title="Docs for agents"
          description="Point your coding agent at our docs so it writes Future AGI code correctly."
        >
          <CodeBlock code="https://docs.futureagi.com/llms.txt" />
          <Note>
            A plain-text index of every docs page, in the llms.txt format.{" "}
            <Link
              href="https://docs.futureagi.com/llms.txt"
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
