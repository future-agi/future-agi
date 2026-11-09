import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import CodeButton from "../code/CodeButton";
import { sandboxListRequests } from "../code/sandboxRequests";
import { useSandboxList } from "./api/queries";
import SandboxErrorState from "./components/SandboxErrorState";
import SandboxIcon from "./components/SandboxIcon";
import SandboxPageFrame from "./components/SandboxPageFrame";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import StatePanel from "./components/StatePanel";
import CreateSandboxDrawer from "./create/CreateSandboxDrawer";
import { SANDBOX_SETTINGS_PATH, sandboxPath } from "./lib/links";
import { capReason, createRefusalText } from "./lib/offerCopy";
import SandboxesTable from "./list/SandboxesTable";

const DESCRIPTION = "Isolated environments where agents run code.";
const SETTINGS_PATH = SANDBOX_SETTINGS_PATH;

function NewButton({ onClick, disabled, reason }) {
  const button = (
    <Button
      variant="contained"
      color="primary"
      size="small"
      onClick={onClick}
      disabled={disabled}
      startIcon={<SandboxIcon name="plus" size={16} />}
      data-testid="new-sandbox"
      aria-describedby={reason ? "sandboxes-at-cap" : undefined}
    >
      New sandbox
    </Button>
  );
  if (!disabled || !reason) return button;
  // A disabled button fires no pointer events, so the reason hangs on a wrapper.
  return (
    <Tooltip title={reason} arrow>
      <span>{button}</span>
    </Tooltip>
  );
}

NewButton.propTypes = {
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  reason: PropTypes.string,
};

function AtCapNotice({ active, max, canEdit, onSettings }) {
  return (
    <Alert
      severity="info"
      id="sandboxes-at-cap"
      sx={{ mb: 2 }}
      data-testid="sandboxes-at-cap"
      action={
        canEdit ? (
          <Button
            size="small"
            color="inherit"
            onClick={onSettings}
            data-testid="sandboxes-at-cap-settings"
          >
            Change the limit
          </Button>
        ) : null
      }
    >
      {capReason(active, max)}
      {canEdit ? "" : " A workspace admin can review the limit in Settings."}
    </Alert>
  );
}

AtCapNotice.propTypes = {
  active: PropTypes.number.isRequired,
  max: PropTypes.number.isRequired,
  canEdit: PropTypes.bool,
  onSettings: PropTypes.func.isRequired,
};

/** "1 of 10 sandboxes running in this workspace, plus 1 agent run". */
function activeCountLine(active, max, runningRuns) {
  const line = `${active} visible sandboxes running; workspace limit ${max}`;
  if (runningRuns === 0) return line;
  return `${line}, plus ${runningRuns} visible agent run${runningRuns === 1 ? "" : "s"}`;
}

function ListBody() {
  const navigate = useNavigate();
  const list = useSandboxList();
  const [creating, setCreating] = useState(false);
  const data = list.data;
  // EE already drops deleted customer sandboxes; a finished run's sandbox comes
  // back as destroyed, and a list of past desks is not useful here.
  const sandboxes = (data?.sandboxes ?? []).filter(
    (sandbox) => sandbox.state !== "destroyed",
  );
  const caps = data?.caps;
  const active = sandboxes.filter(
    (s) => s.origin !== "agent_run" && s.state === "active",
  ).length;
  // An agent run's computer shows as a Running row but is not one of the
  // workspace's sandboxes, so the limit does not count it (EE counts the
  // workspace's own). The line names both, so it agrees with the rows.
  const runningRuns = sandboxes.filter(
    (s) => s.origin === "agent_run" && s.state === "active",
  ).length;
  const atCap = Boolean(caps) && active >= caps.maxActiveSandboxes;

  let content;
  if (list.isLoading) content = <LoadingRows />;
  else if (list.error && !data)
    content = (
      <SandboxErrorState error={list.error} onRetry={() => list.refetch()} />
    );
  else if (sandboxes.length === 0) {
    content = (
      <StatePanel
        testId="sandboxes-empty"
        title="No sandboxes yet"
        description="A sandbox is a container of its own for running commands, with files, snapshots and an idle timeout."
        action={<NewButton onClick={() => setCreating(true)} />}
      />
    );
  } else content = <SandboxesTable sandboxes={sandboxes} />;

  return (
    <SandboxPageFrame
      testId="omega-tab-sandboxes"
      title="Sandboxes"
      description={DESCRIPTION}
      meta={
        caps && sandboxes.length > 0 ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="sandboxes-active-count"
          >
            {activeCountLine(active, caps.maxActiveSandboxes, runningRuns)}
          </Typography>
        ) : null
      }
      action={
        caps ? (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <CodeButton
              requests={sandboxListRequests(caps, { active })}
              subject="Sandboxes"
            />
            {caps.settings?.canEdit && (
              <Button
                size="small"
                color="inherit"
                onClick={() => navigate(SETTINGS_PATH)}
                data-testid="sandbox-settings-link"
              >
                Settings
              </Button>
            )}
            {sandboxes.length > 0 && (
              <NewButton
                onClick={() => setCreating(true)}
                disabled={atCap}
                reason={
                  atCap && !caps.createRefusal
                    ? capReason(active, caps.maxActiveSandboxes)
                    : undefined
                }
              />
            )}
          </Box>
        ) : null
      }
    >
      {caps?.createRefusal && (
        <Alert
          severity="warning"
          sx={{ mb: 2 }}
          data-testid="sandboxes-create-refusal"
        >
          New sandboxes cannot be created in this workspace right now.{" "}
          {createRefusalText(caps.createRefusal)}
        </Alert>
      )}
      {atCap && !caps.createRefusal && sandboxes.length > 0 && (
        <AtCapNotice
          active={active}
          max={caps.maxActiveSandboxes}
          canEdit={Boolean(caps.settings?.canEdit)}
          onSettings={() => navigate(SETTINGS_PATH)}
        />
      )}
      {content}
      {caps && (
        <CreateSandboxDrawer
          open={creating}
          caps={caps}
          onClose={() => setCreating(false)}
          onCreated={(sandbox) => {
            setCreating(false);
            navigate(sandboxPath(sandbox.id));
          }}
        />
      )}
    </SandboxPageFrame>
  );
}

export default function SandboxesListPage() {
  return (
    <SessionGateFrame>
      <ListBody />
    </SessionGateFrame>
  );
}

// The gate's own states sit inside the same frame, so the title never jumps.
function SessionGateFrame({ children }) {
  return (
    <SessionGate
      fallbackFrame={(node) => (
        <SandboxPageFrame
          testId="omega-tab-sandboxes"
          title="Sandboxes"
          description={DESCRIPTION}
        >
          {node}
        </SandboxPageFrame>
      )}
    >
      {children}
    </SessionGate>
  );
}

SessionGateFrame.propTypes = { children: PropTypes.node };
