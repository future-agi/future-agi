import React, { useRef, useState } from "react";
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
import SandboxesUnavailable, {
  sandboxesUnavailable,
} from "./components/SandboxesUnavailable";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import StatePanel from "./components/StatePanel";
import CreateSandboxDrawer from "./create/CreateSandboxDrawer";
import { SANDBOX_SETTINGS_PATH, sandboxPath } from "./lib/links";
import { capReason, createRefusalText } from "./lib/offerCopy";
import { STATE_FILTERS, filterSandboxes } from "./list/listView";
import SandboxesTable, { useCardLayout } from "./list/SandboxesTable";
import SandboxesToolbar from "./list/SandboxesToolbar";

const DESCRIPTION = "Isolated environments where agents run code.";
const SETTINGS_PATH = SANDBOX_SETTINGS_PATH;
// Why New sandbox is off when the workspace's settings refuse every create.
const CREATE_BLOCKED =
  "New sandboxes can't be created in this workspace right now.";

function NewButton({ onClick, disabled, reason, describedBy }) {
  const button = (
    <Button
      variant="contained"
      color="primary"
      size="small"
      onClick={onClick}
      disabled={disabled}
      startIcon={<SandboxIcon name="plus" size={16} />}
      data-testid="new-sandbox"
      aria-describedby={reason ? describedBy : undefined}
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
  /** The id of the notice that says why the button is off. */
  describedBy: PropTypes.string,
};

function AtCapNotice({ max, canEdit, onSettings }) {
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
      {capReason(max)}
      {canEdit ? "" : " A workspace admin can review the limit in Settings."}
    </Alert>
  );
}

AtCapNotice.propTypes = {
  max: PropTypes.number.isRequired,
  canEdit: PropTypes.bool,
  onSettings: PropTypes.func.isRequired,
};

/**
 * "You can see 1 running sandbox. This workspace can run 10 at once. Agent
 * runs (1) don't count." A member sees only their own sandboxes, so the count
 * says "you can see", never "this workspace has".
 */
function activeCountLine(active, max, runningRuns) {
  const line = `You can see ${active} running sandbox${active === 1 ? "" : "es"}. This workspace can run ${max} at once.`;
  if (runningRuns === 0) return line;
  return `${line} Agent runs (${runningRuns}) don't count.`;
}

/**
 * The list with its toolbar: search, state chips, Sort by for the cards and
 * counts above the rows, and a clear way back when nothing matches. The sort
 * lives here, so it outlives the no-match panel and a switch between table
 * and cards.
 */
function FilteredList({ sandboxes, running, runningRuns }) {
  const [query, setQuery] = useState("");
  const [states, setStates] = useState([]);
  // No sort until one is chosen: rows keep the API's order.
  const [sort, setSort] = useState(null);
  const frame = useRef(null);
  const cards = useCardLayout(frame);
  const shown = filterSandboxes(sandboxes, { query, states });
  const counts = Object.fromEntries(
    STATE_FILTERS.map((state) => [
      state,
      sandboxes.filter((sandbox) => sandbox.state === state).length,
    ]),
  );
  const clear = () => {
    setQuery("");
    setStates([]);
  };
  const everything =
    sandboxes.length === 1
      ? "the one sandbox"
      : `all ${sandboxes.length} sandboxes`;
  return (
    <Box ref={frame} sx={{ display: "flex", flexDirection: "column" }}>
      <SandboxesToolbar
        query={query}
        onQuery={setQuery}
        states={states}
        onStates={setStates}
        counts={counts}
        shown={shown.length}
        total={sandboxes.length}
        running={running}
        runningRuns={runningRuns}
        sort={sort}
        onSort={setSort}
        sortMenu={cards}
      />
      {shown.length > 0 ? (
        <SandboxesTable
          sandboxes={shown}
          sort={sort}
          onSort={setSort}
          cards={cards}
        />
      ) : (
        <StatePanel
          compact
          testId="sandboxes-no-match"
          title="No sandboxes match these filters"
          description={`Clear the search and state filters to see ${everything} again.`}
          action={
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={clear}
            >
              Clear filters
            </Button>
          }
        />
      )}
    </Box>
  );
}

FilteredList.propTypes = {
  sandboxes: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Running sandboxes of the workspace's own, as the header counts them. */
  running: PropTypes.number.isRequired,
  /** Running agent runs, which the header names apart. */
  runningRuns: PropTypes.number.isRequired,
};

function ListBody() {
  const navigate = useNavigate();
  const list = useSandboxList();
  const [creating, setCreating] = useState(false);
  // Sandboxes are not set up on this deployment: the page's normal answer
  // there, with no list, limit, warning or New sandbox around it.
  const unavailable = sandboxesUnavailable(list);
  if (unavailable) {
    return (
      <SandboxPageFrame
        testId="omega-tab-sandboxes"
        title="Sandboxes"
        description={DESCRIPTION}
      >
        <SandboxesUnavailable reason={unavailable.reason} />
      </SandboxPageFrame>
    );
  }
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
  // The workspace's settings leave nothing creatable: New sandbox stays on the
  // page, off, tied to the refusal shown above the list, since a drawer could
  // only repeat it.
  const blocked = Boolean(caps?.createRefusal);

  let content;
  if (list.isLoading)
    content = <LoadingRows rows={6} label="Loading sandboxes" />;
  else if (list.error && !data)
    content = (
      <SandboxErrorState error={list.error} onRetry={() => list.refetch()} />
    );
  else if (sandboxes.length === 0) {
    content = (
      <StatePanel
        testId="sandboxes-empty"
        title="No sandboxes yet"
        description={
          blocked
            ? "Nothing can be created here until the issue above is resolved."
            : "A sandbox is a container of its own for running commands, with files, snapshots and an idle timeout. Create one here, or start one from your code with the requests under Code."
        }
        action={
          <NewButton
            onClick={() => setCreating(true)}
            disabled={blocked}
            reason={blocked ? CREATE_BLOCKED : undefined}
            describedBy="sandboxes-create-refusal"
          />
        }
      />
    );
  } else {
    content = (
      <FilteredList
        sandboxes={sandboxes}
        running={active}
        runningRuns={runningRuns}
      />
    );
  }

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
                disabled={atCap || blocked}
                reason={
                  blocked
                    ? CREATE_BLOCKED
                    : atCap
                      ? capReason(caps.maxActiveSandboxes)
                      : undefined
                }
                describedBy={
                  blocked ? "sandboxes-create-refusal" : "sandboxes-at-cap"
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
          id="sandboxes-create-refusal"
          sx={{ mb: 2 }}
          data-testid="sandboxes-create-refusal"
          action={
            caps.settings?.canEdit ? (
              <Button
                size="small"
                color="inherit"
                onClick={() => navigate(SETTINGS_PATH)}
                data-testid="sandboxes-create-refusal-settings"
              >
                Open sandbox settings
              </Button>
            ) : null
          }
        >
          New sandboxes cannot be created in this workspace right now.{" "}
          {createRefusalText(caps.createRefusal)}
        </Alert>
      )}
      {atCap && !caps.createRefusal && sandboxes.length > 0 && (
        <AtCapNotice
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
