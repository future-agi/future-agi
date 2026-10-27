import { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ChipToggle, SegmentedToggle } from "../forms/formBits";
import { T12, T13 } from "../drawerStyles";
import { CodeBlock, LocalSteps } from "./startBits";
import {
  AGENT_PROMPT,
  MCP_ADD_CMD,
  PYTHON_DEFINE,
  installCmd,
} from "./fromCode";

const MODES = [
  { value: "cli", label: "CLI" },
  { value: "python", label: "Python" },
  { value: "agent", label: "Your coding agent" },
];

const OS_OPTIONS = [
  { id: "mac", name: "macOS", icon: "logos:apple" },
  { id: "linux", name: "Linux", icon: "logos:linux-tux" },
  { id: "windows", name: "Windows", icon: "logos:microsoft-windows-icon" },
];

/**
 * From code, three ways: the CLI on this machine, Python, or the developer's
 * coding agent over MCP. All three make the same kind of environment, managed
 * in code. The CLI and SDK aren't released yet, so this is the design only.
 */
export default function FromCodeForm() {
  const [mode, setMode] = useState("cli");
  const [os, setOs] = useState("mac");
  const steps = [
    {
      title: "Install the CLI",
      body: "Once per machine. It adds fai to your path.",
      extra: (
        <Stack direction="row" spacing={0.75} sx={{ mb: 1 }}>
          {OS_OPTIONS.map((o) => (
            <ChipToggle
              key={o.id}
              icon={o.icon}
              label={o.name}
              on={os === o.id}
              onClick={() => setOs(o.id)}
            />
          ))}
        </Stack>
      ),
      cmd: installCmd(os),
    },
    {
      title: "Initialize for your agent",
      body: "Every environment belongs to one agent in Agents. Name it here.",
      hint: "--version picks the agent's version (its production one by default), --scenarios 10 to 1,000.",
      cmd: 'fai env init my-environment --agent "My agent" --scenarios 10',
    },
    {
      title: "Build it on your machine",
      body: "Builds the world locally and opens a secure tunnel back to this workspace.",
      hint: "Nothing in the world leaves your machine. The tunnel carries results, traces and scores.",
      cmd: "fai env up my-environment",
    },
  ];

  return (
    <Box>
      <Box sx={{ px: 2.5, pt: 2.5 }}>
        <SegmentedToggle
          label="From code"
          value={mode}
          onChange={setMode}
          options={MODES}
        />
      </Box>
      <Box sx={{ p: 2.5 }}>
        {mode === "cli" && <LocalSteps steps={steps} />}
        {mode === "python" && (
          <Stack spacing={1.5}>
            <Typography sx={{ ...T13, color: "text.secondary" }}>
              Define the environment next to your agent and run it from Python.
              Install with pip install futureagi.
            </Typography>
            <CodeBlock code={PYTHON_DEFINE} />
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              Every setting in the builder has a code form. Without push() it
              still runs; push() gives it versions and a place in My
              environments.
            </Typography>
          </Stack>
        )}
        {mode === "agent" && (
          <Stack spacing={2}>
            <LocalSteps
              steps={[
                {
                  title: "Add Future AGI to your coding agent",
                  body: "Claude Code shown; Cursor, VS Code and Windsurf are in Settings → MCP Server. Once per machine.",
                  cmd: MCP_ADD_CMD,
                },
              ]}
            />
            <Box>
              <Typography sx={{ ...T13, fontWeight: 500, mb: 0.75 }}>
                Then ask it, in your agent&apos;s repo
              </Typography>
              <CodeBlock code={AGENT_PROMPT} wrap />
            </Box>
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              It reads your agent, writes the environment and scenarios next to
              your code, runs it and proposes fixes. What it builds shows up
              here.
            </Typography>
          </Stack>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:laptop-2-linear"
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography sx={{ ...T12, color: "text.secondary", flex: 1 }}>
          {mode === "cli"
            ? "Once fai env up connects, the environment shows up in My environments."
            : "It shows up in My environments, marked Managed in code."}
        </Typography>
      </Stack>
    </Box>
  );
}
