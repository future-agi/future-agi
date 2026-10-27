import PropTypes from "prop-types";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import SideDrawer from "../../components/SideDrawer";
import CopyField from "../../components/CopyField";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import CodeSnippet from "../../buildDrawer/forms/shared/CodeSnippet";
import RailStep from "../../buildDrawer/forms/shared/RailStep";
import {
  agentPrompt,
  apiSnippet,
  ciWorkflow,
  cliSteps,
  pythonOwnAgent,
  pythonRun,
  pythonTrain,
  serveCmd,
} from "./snippets";

// Conversations a run starts at once, as the snippets state it.
const PARALLEL = 20;

const TABS = [
  { id: "api", label: "API" },
  { id: "cli", label: "CLI" },
  { id: "python", label: "Python" },
  { id: "ci", label: "CI" },
  { id: "mcp", label: "Your agent (MCP)" },
  { id: "train", label: "Train (RL)" },
];

function Label({ children, flagged = false }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
      <Typography sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500 }}>
        {children}
      </Typography>
      {flagged && <UiOnlyFlag feature="runFromCode" />}
    </Stack>
  );
}
Label.propTypes = { children: PropTypes.node, flagged: PropTypes.bool };

const Note = ({ children }) => (
  <Typography
    sx={{ fontSize: 12, lineHeight: "16px", color: "text.subtitle", mt: 1 }}
  >
    {children}
  </Typography>
);
Note.propTypes = { children: PropTypes.node };

// The header's Code button and Vel's "Run from code" drawer: the same run as
// Run simulation, from the API, the terminal, Python, CI, a coding agent or a
// training loop.
export default function RunFromCodeButton({ env, repeats }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("api");
  const id = env?.id || "env_...";

  return (
    <>
      <CustomTooltip
        show
        size="small"
        arrow
        title="Run from code: API, CLI, Python, CI or MCP"
      >
        <Button
          size="small"
          variant="outlined"
          aria-label="Run from code"
          onClick={() => setOpen(true)}
          startIcon={<Iconify icon="solar:code-linear" width={16} />}
          sx={{
            whiteSpace: "nowrap",
            minWidth: 0,
            color: "text.primary",
            borderColor: "divider",
          }}
        >
          Code
        </Button>
      </CustomTooltip>

      <SideDrawer
        open={open}
        onClose={() => setOpen(false)}
        width={{ xs: "100%", md: 640 }}
        hideClose
      >
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
                sx={{
                  fontSize: 13,
                  lineHeight: "20px",
                  color: "text.subtitle",
                }}
              >
                The same run as Run simulation, from the API, your terminal,
                code, CI or your own agent, or driven from a training loop.
              </Typography>
            </Box>
            <IconButton
              size="small"
              onClick={() => setOpen(false)}
              aria-label="Close"
            >
              <Iconify icon="solar:close-linear" width={16} />
            </IconButton>
          </Stack>

          <Box
            sx={{ px: 2.5, py: 2, flex: 1, minHeight: 0, overflowY: "auto" }}
          >
            <Stack spacing={2.5}>
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
                  icon="solar:widget-linear"
                  width={16}
                  sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
                />
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography
                      sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500 }}
                    >
                      Managed here
                    </Typography>
                    <Typography
                      sx={{
                        fontSize: 12,
                        color: "text.subtitle",
                        fontFamily: "monospace",
                      }}
                    >{`· ${id}`}</Typography>
                    <UiOnlyFlag feature="runFromCode" />
                  </Stack>
                  <Typography
                    sx={{
                      fontSize: 13,
                      lineHeight: "20px",
                      color: "text.subtitle",
                    }}
                  >
                    Edits here make new versions. To keep it in your repo, pull
                    it into your project. From then on it&apos;s managed in
                    code.
                  </Typography>
                  <Box sx={{ mt: 1 }}>
                    <CopyField value={`fai env pull ${id}`} />
                  </Box>
                </Box>
              </Stack>

              <ToggleButtonGroup
                exclusive
                size="small"
                value={tab}
                onChange={(_, v) => v && setTab(v)}
                aria-label="Run from"
                sx={{
                  alignSelf: "flex-start",
                  p: "2px",
                  bgcolor: "background.neutral",
                  "& .MuiToggleButton-root": {
                    border: 0,
                    borderRadius: "4px !important",
                    px: 1,
                    py: 0.25,
                    fontSize: 12,
                    lineHeight: "20px",
                    textTransform: "none",
                    color: "text.secondary",
                  },
                  "& .Mui-selected": {
                    color: "text.primary",
                    bgcolor: "background.paper",
                  },
                }}
              >
                {TABS.map((t) => (
                  <ToggleButton key={t.id} value={t.id}>
                    {t.label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>

              {tab === "api" && (
                <Box>
                  <Label>Start a run over HTTP</Label>
                  <CodeSnippet code={apiSnippet(id, repeats)} />
                  <Note>
                    Use an API key and secret from Settings. The run shows up
                    here like one started with Run simulation.
                  </Note>
                </Box>
              )}

              {tab === "cli" && (
                <Box>
                  <Label flagged>Run it from your terminal</Label>
                  {cliSteps(id, repeats, PARALLEL).map((step, i, all) => (
                    <RailStep
                      key={step.title}
                      n={i + 1}
                      title={step.title}
                      body={step.body}
                      last={i === all.length - 1}
                    >
                      <CodeSnippet code={step.cmd} />
                      {step.hint && <Note>{step.hint}</Note>}
                    </RailStep>
                  ))}
                </Box>
              )}

              {tab === "python" && (
                <Stack spacing={2}>
                  <Box>
                    <Label flagged>Run it and stream the results</Label>
                    <CodeSnippet code={pythonRun(id, repeats, PARALLEL)} />
                  </Box>
                  <Box>
                    <Label flagged>
                      Or test your agent from code, without deploying it
                    </Label>
                    <CodeSnippet code={pythonOwnAgent(repeats)} />
                  </Box>
                </Stack>
              )}

              {tab === "ci" && (
                <Box>
                  <Label flagged>
                    Block a pull request when the pass rate drops
                  </Label>
                  <CodeSnippet code={ciWorkflow(id, repeats, PARALLEL)} />
                  <Note>
                    The run shows up here with its commit and pull request.
                    --fail-under exits 1 below the pass rate you set.
                  </Note>
                </Box>
              )}

              {tab === "mcp" && (
                <Box>
                  <Label flagged>Ask your coding agent</Label>
                  <CodeSnippet code={agentPrompt(env?.name, repeats)} />
                  <Note>
                    Runs, failures and fixes from your coding agent. Connect it
                    once in{" "}
                    <Link
                      component={RouterLink}
                      to={paths.dashboard.settings.mcpServer}
                      underline="always"
                      sx={{ color: "inherit" }}
                    >
                      Settings, MCP Server
                    </Link>
                    .
                  </Note>
                </Box>
              )}

              {tab === "train" && (
                <Stack spacing={2}>
                  <Box>
                    <Label flagged>
                      Drive episodes from your training loop
                    </Label>
                    <CodeSnippet code={pythonTrain(id)} />
                  </Box>
                  <Box>
                    <Label flagged>
                      Or serve copies to a trainer on another machine
                    </Label>
                    <CodeSnippet code={serveCmd(id)} />
                  </Box>
                  <Note>
                    The reward is this environment&apos;s evals. Works with
                    OpenEnv, TRL, verifiers and gym-style loops.
                  </Note>
                </Stack>
              )}
            </Stack>
          </Box>
        </Stack>
      </SideDrawer>
    </>
  );
}

RunFromCodeButton.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  repeats: PropTypes.number.isRequired,
};
