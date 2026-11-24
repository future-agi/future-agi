import { Stack, Typography } from "@mui/material";

import CodeBlock from "src/components/code-block";
import Section from "src/components/section";
import { useCodingAgentAttemptsEnabled } from "src/api/simulate-environments/codingAgent";

const STEPS = [
  {
    title: "1. Add the MCP server to your coding agent",
    body: "Follow Connect Your IDE above. Claude Code, Cursor and Codex each have their own steps.",
  },
  {
    title: "2. Install the Future AGI CLI",
    body: "Or run it without installing: put npx @future-agi/cli in place of fagi.",
    code: "npm install -g @future-agi/cli",
  },
  {
    title: "3. Sign in",
    code: "fagi login",
  },
];

// How a person's own coding agent fixes a failed run: the MCP server and the `fagi`
// CLI, on its own login. Shown only while the deployment lets coding agents submit
// attempts.
export default function CodingAgentSetup() {
  const enabled = useCodingAgentAttemptsEnabled();
  if (!enabled) return null;
  return (
    <Section
      variant="panel"
      data-testid="coding-agent-setup"
      title="IDE & coding agents"
      description="Let Claude Code, Cursor or Codex fix a failed run. On an Epoch or an Error Feed issue, choose Copy for coding agent and paste the prompt into your agent."
      sx={{ p: 3, mb: 3 }}
    >
      <Stack spacing={2}>
        {STEPS.map((step) => (
          <Stack key={step.title} spacing={0.75}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {step.title}
            </Typography>
            {step.body && (
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                {step.body}
              </Typography>
            )}
            {step.code && <CodeBlock code={step.code} />}
          </Stack>
        ))}
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Each fix your agent submits with fagi epoch submit is an attempt.
          Future AGI runs it as a short Epoch, checks it on cases your agent
          wasn&apos;t given, and sends back a verdict.
        </Typography>
      </Stack>
    </Section>
  );
}
