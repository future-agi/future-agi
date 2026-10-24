import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Dialog, IconButton, Stack, Typography } from "@mui/material";
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

/**
 * Connect agent to fix (RSI PRD, Flow 3), over the issue — never a page
 * away from it. A dialog sized to what it holds: it opens on what the
 * issue's Observe project already says about the agent behind it and the
 * one right way in, then grows into that flow, with the project already
 * linked. Saving closes it and the issue's button reads Fix.
 *
 * A project whose traces don't say which agent sent them opens straight on
 * the full list of ways to connect one.
 */
const mono = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };

export default function ConnectAgentDialog({ open, onClose, issue, project }) {
  const { enqueueSnackbar } = useSnackbar();
  const save = useSaveConnectedAgent();
  const known = observeProjectById(project);
  const projectName = known?.name || project || "this issue's project";
  const traced = known?.traced || null;
  const issueId = issue?.cluster_id || null;

  /* null: what the traces say · "detected": the one right way · "all": every way. */
  const [mode, setMode] = useState(null);
  const [request, setRequest] = useState(null);
  useEffect(() => {
    if (!open) return;
    setMode(traced ? null : "all");
    setRequest(null);
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
        {mode === null && traced && (
          <IssueConnectCard
            bare
            issueId={issueId}
            project={known}
            onConnectDetected={() => {
              setRequest(detectedRequestOf(traced));
              setMode("detected");
            }}
            onAnotherWay={() => setMode("all")}
          />
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
              {traced && (
                <TextLink
                  onClick={() => setMode(null)}
                >{`From its ${traced.via === "traceai" ? "traces" : "call logs"}`}</TextLink>
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
