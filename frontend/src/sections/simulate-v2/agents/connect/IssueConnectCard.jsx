import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { FRAMEWORK_META, PROVIDER_LABELS } from "../../_mock/agentRegistry";
import { TextLink } from "./connectBits";

const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

/**
 * Connect agent to fix, before any form: what the project's traces already
 * say about the agent behind them, and the one right way in —
 *   traceai   → its repository (code agents come in through git; we read the
 *               framework and find the agent there)
 *   call-logs → that platform's assistant, by the ID in the logs, with the
 *               key Observe's integration already holds (by reference)
 * The agent is never picked from the issue: one already in Agents is found
 * again when it's connected the same way (and this project replaces the one
 * it had — one project per agent), or linked from its own Connections tab. "Connect another way" opens the full list.
 */
export default function IssueConnectCard({
  issueId,
  project,
  onConnectDetected,
  onAnotherWay,
  anotherWayLabel = "Connect another way",
  bare = false,
  aside = null,
  /* "Your agents" is listed above it — no need to point at Agents. */
  existingListed = false,
}) {
  const navigate = useNavigate();
  const traced = project.traced;
  const fw = traced.framework ? FRAMEWORK_META[traced.framework] : null;
  const providerLabel = traced.provider
    ? PROVIDER_LABELS[traced.provider] || traced.provider
    : null;
  const rows =
    traced.via === "traceai"
      ? [
          [
            "Sent by",
            `traceAI · ${fw?.label || traced.framework} · ${traced.language}`,
          ],
          ["Model", traced.model],
          ["Tools", traced.tools.join(", "), true],
        ]
      : [
          ["Sent by", `${providerLabel} call logs · ${traced.integration}`],
          ...(traced.agentName ? [["Assistant", traced.agentName]] : []),
          [traced.agentName ? "ID" : "Assistant", traced.agentId, true],
          ["Channel", traced.agentType === "chat" ? "Chat" : "Voice"],
        ];
  const why = `Its ${traced.via === "traceai" ? "traces" : "call logs"} reach ${project.name}, but no agent in Agents is linked to it — fixing it needs the agent itself to run simulations and ship a draft. Connect it once: ${project.name} comes with it, and Fix works for every issue from it.`;

  return (
    <Box
      sx={
        bare
          ? null
          : {
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              bgcolor: "background.paper",
              overflow: "hidden",
            }
      }
    >
      {bare ? (
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            px: 2.5,
            pt: 2,
            pb: 1.75,
          }}
        >
          {why}
        </Typography>
      ) : (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.25}
          sx={{ px: 2.5, pt: 2, pb: 1.5 }}
        >
          <Iconify
            icon="solar:bug-linear"
            width={20}
            sx={{ color: "text.secondary", flexShrink: 0, mt: 0.25 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {`Connect the agent behind ${issueId || project.name}`}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
            >
              {why}
            </Typography>
          </Box>
          {aside}
        </Stack>
      )}

      {/* What the traces already told us. */}
      <Box
        sx={{
          mx: 2.5,
          mb: 2,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.subtitle",
            textTransform: "none",
            letterSpacing: 0.5,
            px: 1.5,
            pt: 1,
            pb: 0.5,
          }}
        >
          {traced.via === "traceai" ? "From its traces" : "From its call logs"}
        </Typography>
        {rows.map(([k, v, isMono]) => (
          <Stack
            key={k}
            direction="row"
            spacing={2}
            sx={{
              px: 1.5,
              py: 0.75,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                typography: "s2",
                color: "text.subtitle",
                width: 90,
                flexShrink: 0,
              }}
            >
              {k}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", minWidth: 0, ...(isMono && mono) }}
            >
              {v}
            </Typography>
          </Stack>
        ))}
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={onConnectDetected}
          startIcon={
            <Iconify
              icon={
                traced.via === "traceai"
                  ? "mdi:github"
                  : "solar:link-circle-linear"
              }
              width={16}
            />
          }
          sx={{ flexShrink: 0 }}
        >
          {traced.via === "traceai"
            ? "Connect its repository"
            : `Connect ${providerLabel} assistant`}
        </Button>
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            flex: 1,
            minWidth: 0,
          }}
        >
          {traced.via === "traceai"
            ? `We read the code, find the ${fw?.label || ""} agent and confirm it with you. Fixes ship as draft pull requests.`
            : `Uses the key your ${traced.integration} already holds — by reference, never copied. Fixes ship as a ${traced.provider === "retell" ? "Retell draft version" : `${providerLabel} copy`}.`}
        </Typography>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, pb: 1.5 }}
      >
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            flex: 1,
            minWidth: 0,
          }}
        >
          {existingListed
            ? "Or another way — its code, the platform it runs on, or its endpoint."
            : "Already in Agents? Connect it the same way to link this project, or change it on the agent's Connections tab."}
        </Typography>
        {!existingListed && (
          <TextLink onClick={() => navigate(paths.dashboard.agents)}>
            Open Agents
          </TextLink>
        )}
        <TextLink onClick={onAnotherWay}>{anotherWayLabel}</TextLink>
      </Stack>
    </Box>
  );
}

IssueConnectCard.propTypes = {
  issueId: PropTypes.string,
  /* An Observe project with `traced` — what its traces or call logs say. */
  project: PropTypes.object.isRequired,
  onConnectDetected: PropTypes.func.isRequired,
  onAnotherWay: PropTypes.func.isRequired,
  anotherWayLabel: PropTypes.string,
  /* Inside a drawer whose header already names it: no frame, no title. */
  bare: PropTypes.bool,
  /* Shown beside the title on the page — a way back to the issue. */
  aside: PropTypes.node,
  existingListed: PropTypes.bool,
};
