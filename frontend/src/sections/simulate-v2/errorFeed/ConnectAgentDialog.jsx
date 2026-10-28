import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { PROVIDER_LABELS } from "../_mock/agentRegistry";
import { observeProjectById } from "../_mock/connectAgent";
import { InDrawerContext } from "../environments/connectMode";
import { LogoTile } from "../environments/LandingBits";
import ConnectAgentPicker from "../agents/connect/ConnectAgentPicker";
import IssueConnectCard from "../agents/connect/IssueConnectCard";
import { TextLink } from "../agents/connect/connectBits";
import { detectedRequestOf } from "../agents/connect/detectedRequest";
import { useSaveConnectedAgent } from "../agents/connect/useSaveConnectedAgent";
import YourAgentsList from "../agents/connect/YourAgentsList";
import { useConnectedAgents } from "../store";

/**
 * Connect agent to fix (RSI PRD, Flow 3), over the issue — never a page
 * away from it. A dialog sized to what it holds: it opens on what the
 * issue's Observe project already says about the agent behind it and the
 * one right way in, then grows into that flow, with the project already
 * linked. Saving closes it and the issue's button reads Fix.
 *
 * It opens on the agents already in Agents — the ones the traces point at
 * first — since a traced app is often an agent that's here already; picking
 * one saves the project on it. Below, connecting a new one: the one right
 * way the traces show, else every way. With no agents yet, it opens on that.
 */
const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

export default function ConnectAgentDialog({ open, onClose, issue, project }) {
  const { enqueueSnackbar } = useSnackbar();
  const save = useSaveConnectedAgent();
  const known = observeProjectById(project);
  const projectName = known?.name || project || "this issue's project";
  const traced = known?.traced || null;
  const issueId = issue?.cluster_id || null;
  const { agents } = useConnectedAgents();
  const hasAgents = agents.some((a) => !a?.deleted?.at);

  /* null: what the traces say · "detected": the one right way · "all": every way. */
  const [mode, setMode] = useState(null);
  const [request, setRequest] = useState(null);
  useEffect(() => {
    if (!open) return;
    setMode(traced || hasAgents ? null : "all");
    setRequest(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, traced]);

  const prefill = useMemo(
    () => ({ project: project || null, issue: issueId }),
    [project, issueId],
  );

  const onConnect = useCallback(
    (source) => {
      const saved = save(source);
      onClose();
      enqueueSnackbar(
        `${saved.name} ${saved.exists ? "updated" : "connected"} · ${projectName} is linked, so this issue can be fixed`,
        { variant: "success" },
      );
    },
    [save, onClose, enqueueSnackbar, projectName],
  );

  const providerLabel = traced?.provider
    ? PROVIDER_LABELS[traced.provider] || traced.provider
    : null;
  const meta =
    mode === "detected" && traced
      ? {
          logo: traced.via === "traceai" ? "github" : traced.provider,
          name:
            traced.via === "traceai"
              ? "Connect its repository"
              : `Connect ${providerLabel} assistant`,
          detail: issueId,
        }
      : {
          icon: "solar:bug-linear",
          name: "Connect agent to fix",
          detail: issueId,
        };

  return (
    <Dialog
      open={!!open}
      onClose={onClose}
      scroll="paper"
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 960,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <LogoTile
          logo={meta.logo}
          icon={meta.icon || "solar:link-circle-linear"}
          size={36}
        />
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1}
          sx={{ flex: 1, minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{ typography: "m3", fontWeight: 600, flexShrink: 0 }}
          >
            {meta.name}
          </Typography>
          {meta.detail && (
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary", ...mono }}
            >
              {meta.detail}
            </Typography>
          )}
        </Stack>
        <IconButton
          size="small"
          onClick={onClose}
          aria-label="Close"
          sx={{ flexShrink: 0 }}
        >
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          /* A flow's Continue row stays in reach at the bottom. */
          "& .continue-row": {
            position: "sticky",
            bottom: 0,
            zIndex: 2,
            bgcolor: "background.paper",
            pt: 1.5,
            pb: 1.5,
            mt: 1,
          },
        }}
      >
        {mode === null && project && (
          <YourAgentsList
            project={project}
            onLinked={(agent) => {
              onClose();
              enqueueSnackbar(
                `${projectName} is linked to ${agent.name} — this issue can be fixed now`,
                { variant: "success" },
              );
            }}
          />
        )}
        {mode === null && traced && (
          <Box
            sx={{
              borderTop: hasAgents ? "1px solid" : "none",
              borderColor: "divider",
              pt: hasAgents ? 1 : 0,
            }}
          >
            {hasAgents && (
              <Typography
                sx={{ typography: "s1", fontWeight: 500, px: 2.5, pt: 1.5 }}
              >
                Or connect it
              </Typography>
            )}
            <IssueConnectCard
              bare
              existingListed={hasAgents}
              issueId={issueId}
              project={known}
              onConnectDetected={() => {
                setRequest(detectedRequestOf(traced));
                setMode("detected");
              }}
              onAnotherWay={() => setMode("all")}
            />
          </Box>
        )}
        {mode === null && !traced && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                Or connect it
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Not in Agents yet — connect its code, the platform it runs on,
                or its endpoint.
              </Typography>
            </Box>
            <Button
              size="small"
              variant="outlined"
              onClick={() => setMode("all")}
              sx={{
                flexShrink: 0,
                color: "text.primary",
                borderColor: "divider",
              }}
            >
              Choose how
            </Button>
          </Stack>
        )}
        {mode !== null && (
          <>
            {/* Where this is going — and, from a detected way, back to the traces. */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{
                px: 2.5,
                py: 1.25,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Iconify
                icon="solar:link-minimalistic-2-linear"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {`${projectName} is linked to the agent you connect — then every issue from it can be fixed.`}
              </Typography>
              {(traced || hasAgents) && (
                <TextLink onClick={() => setMode(null)}>
                  {hasAgents
                    ? "Back to your agents"
                    : `From its ${traced.via === "traceai" ? "traces" : "call logs"}`}
                </TextLink>
              )}
            </Stack>
            <InDrawerContext.Provider value>
              <Box sx={{ p: 2 }}>
                <ConnectAgentPicker
                  flowsIn="inline"
                  onConnect={onConnect}
                  prefill={prefill}
                  request={mode === "detected" ? request : null}
                  catalogue={mode === "all"}
                />
              </Box>
            </InDrawerContext.Provider>
          </>
        )}
      </Box>
    </Dialog>
  );
}

ConnectAgentDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  issue: PropTypes.object,
  /* The issue's Observe project id. */
  project: PropTypes.string,
};
