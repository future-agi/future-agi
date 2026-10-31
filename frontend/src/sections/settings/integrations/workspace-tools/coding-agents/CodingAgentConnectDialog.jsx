import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  IMPROVER_TOOLS,
  connectCodingAgent,
  improverTokenLabel,
  useIntegrations,
} from "../store";
import BrandMark from "../BrandMark";
import { Returning } from "../ExternalPage";
import { clickable, focusRing } from "../clickable";
import {
  INSTRUCTIONS,
  PLUGIN_INSTALL,
  IMPROVE_MCP_URL,
  WAYS,
  instructionsFileFor,
  mcpConfigFor,
} from "./codingAgentSetup";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Connecting a coding agent happens on the developer's machine — a plugin
 * install or an MCP config — and finishes when the agent first signs in to
 * the workspace. This shows what to run, then waits for that sign-in:
 *   setup    → pick the way (Claude Code only) and run the steps
 *   waiting  → the agent opens Future AGI in the browser to link the workspace
 *   done     → connected for you, with a scoped improver token; Copy for
 *              coding agent becomes Open in … for you (teammates connect
 *              their own)
 */
export default function CodingAgentConnectDialog({ open, onClose, agent }) {
  const [via, setVia] = useState("plugin");
  const [phase, setPhase] = useState("setup");
  const { codingAgents = {} } = useIntegrations();

  useEffect(() => {
    if (!open || !agent) return;
    setVia(agent.ways[0]);
    setPhase("setup");
  }, [open, agent]);

  /* The OAuth hand-shake, out of sight. In the product this waits for the
     agent's first authenticated call. */
  useEffect(() => {
    if (phase !== "waiting") return undefined;
    const t = setTimeout(() => {
      connectCodingAgent(agent.id, via);
      setPhase("done");
    }, 1400);
    return () => clearTimeout(t);
  }, [phase, agent, via]);

  if (!agent) return null;
  const mcp = mcpConfigFor(agent.id);
  const signIn = (
    <Step n={via === "plugin" ? 2 : 3} title="Sign in when it asks">
      {`The first time ${agent.label} calls Future AGI it opens your browser to link this workspace, and gets its own scoped improver token. This dialog updates when it does.`}
    </Step>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 2 }}
      >
        <BrandMark brand={agent.id} size={36} />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "m3", fontWeight: 700 }}
          >{`Connect ${agent.label}`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            For you only. It runs where you code. Future AGI checks every change
            it proposes.
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={18} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 3, pb: 2.5 }}>
        {phase === "setup" && (
          <Stack spacing={2.25}>
            {agent.ways.length > 1 && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {agent.ways.map((w, i) => {
                  const on = via === w;
                  return (
                    <Stack
                      key={w}
                      direction="row"
                      spacing={1.25}
                      alignItems="flex-start"
                      {...clickable(() => setVia(w), {
                        role: "radio",
                        checked: on,
                      })}
                      sx={{
                        ...focusRing,
                        px: 1.5,
                        py: 1.125,
                        cursor: "pointer",
                        borderTop: i ? "1px solid" : "none",
                        borderColor: "divider",
                        bgcolor: (t) =>
                          on
                            ? alpha(t.palette.text.primary, 0.04)
                            : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Iconify
                        icon={
                          on ? "solar:check-circle-bold" : "solar:circle-linear"
                        }
                        width={16}
                        sx={{
                          mt: 0.25,
                          flexShrink: 0,
                          color: on ? "text.primary" : "text.disabled",
                        }}
                      />
                      <Box flex={1} minWidth={0}>
                        <Stack
                          direction="row"
                          alignItems="baseline"
                          spacing={1}
                        >
                          <Typography
                            sx={{ typography: "s2", fontWeight: 700 }}
                          >
                            {WAYS[w].label}
                          </Typography>
                          {i === 0 && (
                            <Typography
                              sx={{ typography: "s3", color: "text.subtitle" }}
                            >
                              Recommended
                            </Typography>
                          )}
                        </Stack>
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {WAYS[w].blurb}
                        </Typography>
                      </Box>
                    </Stack>
                  );
                })}
              </Box>
            )}

            {via === "plugin" ? (
              <Stack spacing={2}>
                <Step n={1} title="Install the plugin in Claude Code">
                  <Code text={PLUGIN_INSTALL} />
                  <Typography
                    sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
                  >
                    {`Bundles the Future AGI skills and the MCP server: ${IMPROVER_TOOLS.map((t) => t.name).join(", ")}.`}
                  </Typography>
                </Step>
                {signIn}
              </Stack>
            ) : (
              <Stack spacing={2}>
                <Step n={1} title="Add the MCP server">
                  <Code text={IMPROVE_MCP_URL} />
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      mt: 1,
                      mb: 0.5,
                    }}
                  >
                    {"Or add it to "}
                    <Box
                      component="span"
                      sx={{ fontFamily: MONO, color: "text.secondary" }}
                    >
                      {mcp.file}
                    </Box>
                  </Typography>
                  <Code text={mcp.snippet} block />
                </Step>
                <Step n={2} title="Add the instructions file">
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mb: 0.75 }}
                  >
                    {`Without the plugin's skills, ${agent.label} learns the loop from your repo: get the brief, change the agent, submit, read the verdict.`}
                  </Typography>
                  <Code
                    text={INSTRUCTIONS}
                    label={instructionsFileFor(agent.id)}
                    block
                    maxHeight={120}
                  />
                </Step>
                {signIn}
              </Stack>
            )}
          </Stack>
        )}

        {phase === "waiting" && (
          <Returning label={`Waiting for ${agent.label} to sign in…`} />
        )}

        {phase === "done" && (
          <Stack spacing={1.5}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Iconify
                icon="solar:check-circle-bold"
                width={16}
                sx={{ color: "text.primary", flexShrink: 0 }}
              />
              <Typography
                sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
              >{`${agent.label} connected`}</Typography>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle" }}
              >{`via ${WAYS[via].short} · by you`}</Typography>
            </Stack>
            {codingAgents[agent.id]?.token && (
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ px: 1.5 }}
              >
                <Iconify
                  icon="solar:key-minimalistic-linear"
                  width={14}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  <Box
                    component="span"
                    sx={{ fontFamily: MONO, color: "text.secondary" }}
                  >
                    {improverTokenLabel(codingAgents[agent.id].token)}
                  </Box>
                  {
                    " · scoped to briefs, visible scenarios, submit and verdicts · held-back scenarios hidden"
                  }
                </Typography>
              </Stack>
            )}
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {`For you, Copy for coding agent now reads Open in ${agent.label} on Error Feed issues, run diagnoses and improvements. ${agent.label} fetches the brief when you ask it to. Teammates connect their own.`}
            </Typography>
          </Stack>
        )}
      </Box>

      {phase !== "waiting" && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {phase === "setup" && (
            <>
              <Button
                size="small"
                onClick={onClose}
                sx={{ color: "text.secondary" }}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => setPhase("waiting")}
              >
                {via === "plugin" ? "I've installed it" : "I've added it"}
              </Button>
            </>
          )}
          {phase === "done" && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onClose}
            >
              Done
            </Button>
          )}
        </Stack>
      )}
    </Dialog>
  );
}

CodingAgentConnectDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object,
};

function Step({ n, title, children }) {
  return (
    <Stack direction="row" spacing={1.25} alignItems="flex-start">
      <Box
        sx={{
          width: 18,
          height: 18,
          mt: 0.125,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
          border: "1px solid",
          borderColor: "text.primary",
        }}
      >
        <Typography sx={{ fontSize: 10, fontWeight: 700 }}>{n}</Typography>
      </Box>
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.75 }}>
          {title}
        </Typography>
        {typeof children === "string" ? (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {children}
          </Typography>
        ) : (
          children
        )}
      </Box>
    </Stack>
  );
}
Step.propTypes = {
  n: PropTypes.number,
  title: PropTypes.string,
  children: PropTypes.node,
};

/* A command, URL or file to copy. `label` names the file it belongs in. */
function Code({ text, label, block = false, maxHeight }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = () => {
    try {
      navigator.clipboard?.writeText(text);
    } catch {
      /* clipboard unavailable */
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1400);
  };
  const button = (
    <CustomTooltip
      show
      size="small"
      arrow
      title={copied ? "Copied" : "Copy"}
      placement="top"
    >
      <IconButton
        size="small"
        aria-label={copied ? "Copied" : "Copy"}
        onClick={copy}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      >
        <Iconify
          icon={copied ? "solar:check-circle-bold" : "solar:copy-linear"}
          width={14}
        />
      </IconButton>
    </CustomTooltip>
  );
  return (
    <Box
      sx={{
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        overflow: "hidden",
      }}
    >
      {label && (
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            pl: 1.25,
            pr: 0.5,
            py: 0.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontFamily: MONO,
              color: "text.secondary",
              flex: 1,
            }}
          >
            {label}
          </Typography>
          {button}
        </Stack>
      )}
      <Stack
        direction="row"
        alignItems={block ? "flex-start" : "center"}
        sx={{ pl: 1.25, pr: 0.5, py: block ? 1 : 0.25 }}
      >
        <Box
          component={block ? "pre" : "span"}
          sx={{
            m: 0,
            flex: 1,
            minWidth: 0,
            fontFamily: MONO,
            fontSize: 12.5,
            lineHeight: 1.6,
            whiteSpace: block ? "pre-wrap" : "nowrap",
            overflow: "auto",
            maxHeight,
            color: "text.primary",
          }}
        >
          {text}
        </Box>
        {!label && button}
      </Stack>
    </Box>
  );
}
Code.propTypes = {
  text: PropTypes.string,
  label: PropTypes.string,
  block: PropTypes.bool,
  maxHeight: PropTypes.number,
};
