import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { loadHubDeploymentSource } from "../../hub/api/deploymentSource";
import {
  useDiscardHubWizard,
  useHubDraftRef,
  useHubDraftState,
} from "../../hub/components/HubDraftOwner";

/** No create call: a mounted wizard owns its draft until the person replaces it. */
export default function HubSourceGate({
  hubRef,
  workspaceId,
  error,
  load = loadHubDeploymentSource,
  children,
}) {
  const discardWizard = useDiscardHubWizard();
  const [attempt, setAttempt] = useHubDraftState("source.attempt", 0);
  const [result, setResult] = useHubDraftState("source.result", {
    status: "loading",
    ref: hubRef,
    workspaceId,
    bundle: null,
  });
  const locked = useHubDraftRef("source.locked", false);
  const active = useRef(null);
  useEffect(() => {
    // Once a wizard is mounted, a new ref OR workspace needs explicit replacement.
    if (locked.current) return undefined;
    const controller = new AbortController();
    active.current = controller;
    let current = true;
    const context = { ref: hubRef, workspaceId, sourceError: error };
    if (error) {
      setResult({ ...context, status: "error", error });
    } else if (!hubRef) {
      locked.current = true;
      setResult({ ...context, status: "ready", bundle: null });
    } else {
      setResult({ ...context, status: "loading", bundle: null });
      const request = async () => load(hubRef, { signal: controller.signal });
      request()
        .then((bundle) => {
          if (!current || controller.signal.aborted) return;
          locked.current = true;
          setResult({ ...context, status: "ready", bundle });
        })
        .catch((problem) => {
          if (current && !controller.signal.aborted)
            setResult({ ...context, status: "error", error: problem });
        });
    }
    return () => {
      current = false;
      controller.abort();
    };
  }, [hubRef, workspaceId, error, load, attempt]);

  const manual = () => {
    active.current?.abort();
    locked.current = true;
    setResult({
      status: "ready",
      ref: hubRef,
      workspaceId,
      sourceError: error,
      bundle: null,
    });
  };
  const replace = () => {
    active.current?.abort();
    discardWizard();
    locked.current = false;
    setResult({ status: "loading", ref: hubRef, workspaceId, bundle: null });
    setAttempt((value) => value + 1);
  };
  const ready = result.status === "ready";
  const workspaceChanged = ready && result.workspaceId !== workspaceId;
  const refChanged =
    ready && (result.ref !== hubRef || result.sourceError !== error);
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {workspaceChanged ? (
        <Alert severity="warning" data-testid="hub-source-workspace-changed">
          Your draft is paused because the workspace changed. Return to its
          workspace to continue, or discard it and load this source here.
          <Button onClick={replace} data-testid="hub-source-workspace-replace">
            Discard draft and use this workspace
          </Button>
        </Alert>
      ) : (
        refChanged && (
          <Alert severity="info" data-testid="hub-source-changed">
            The Hub link changed. Your current draft has not been replaced.
            <Button onClick={replace} data-testid="hub-source-replace">
              Replace current draft
            </Button>
          </Alert>
        )
      )}
      {ready ? (
        <Box
          key={attempt}
          component="fieldset"
          disabled={workspaceChanged}
          hidden={workspaceChanged}
          aria-hidden={workspaceChanged || undefined}
          inert={workspaceChanged ? "" : undefined}
          sx={{
            border: 0,
            p: 0,
            m: 0,
            minWidth: 0,
            display: workspaceChanged ? "none" : "block",
          }}
        >
          {children(result.bundle, { workspaceChanged })}
        </Box>
      ) : (
        <>
          {result.status === "loading" ? (
            <Typography role="status" data-testid="hub-source-loading">
              Checking the pinned Hub source…
            </Typography>
          ) : (
            <Alert severity="error" data-testid="hub-source-error">
              {result.error?.message || "The Hub source could not be loaded."}{" "}
              This source check did not create a deployment.
            </Alert>
          )}
          <Box sx={{ display: "flex", gap: 1 }}>
            {result.status === "error" && !error && (
              <Button onClick={replace} data-testid="hub-source-retry">
                Retry Hub source
              </Button>
            )}
            <Button onClick={manual} data-testid="hub-source-manual">
              {result.status === "loading"
                ? "Cancel and upload manually"
                : "Upload a project manually"}
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
}
HubSourceGate.propTypes = {
  hubRef: PropTypes.string,
  workspaceId: PropTypes.string,
  error: PropTypes.object,
  load: PropTypes.func,
  children: PropTypes.func.isRequired,
};
