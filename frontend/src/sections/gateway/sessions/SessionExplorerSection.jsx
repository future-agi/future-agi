import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useSearchParams } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";
import { getErrorMessage } from "../../settings/integrations/utils";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import GroupByToggle from "../analytics/GroupByToggle";
import { loadColumnState } from "../components/frame/column-state";
import FaIcon from "../providers/ui/FaIcon";
import { formatLogTime } from "../logs/logDates";
import { formatMs } from "../logs/logStatus";
import { EMPTY, costTotal } from "../utils/formatters";
import {
  SESSIONS_PAGE_SIZE,
  useCloseSession,
  useSessions,
} from "./hooks/useSessions";
import ConfirmDialog from "../components/ConfirmDialog";
import WhyDisabled from "../components/WhyDisabled";
import { useWorkspaceWrite } from "../hooks/useWorkspaceWrite";
import SessionDetailDrawer from "./SessionDetailDrawer";
import { readSessionQuery, updateSessionQuery } from "./queryState";
import {
  ownName,
  sessionStartedAt,
  sessionStatus,
  sessionStatusHelp,
} from "./sessionStatus";

const STATE_KEY = "gateway-sessions";

// Sessions: conversations the gateway grouped by the x-agentcc-session-id
// header, with each one's requests, tokens, cost and latency. Status and
// search filter on the server; the API pages, so the page reads 100 at a
// time and says how many there are. A row opens the session's requests.

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
];

function Clipped({ value, secondary, title }) {
  return (
    <Box
      component="span"
      title={title || (typeof value === "string" ? value : undefined)}
      sx={{
        display: "block",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        fontVariantNumeric: "tabular-nums",
        ...(secondary && { color: "text.secondary" }),
      }}
    >
      {value === "" || value == null ? EMPTY : value}
    </Box>
  );
}

Clipped.propTypes = {
  value: PropTypes.node,
  secondary: PropTypes.bool,
  title: PropTypes.string,
};

// A session's cost in a grid cell, from its stats' unpriced request count.
const sessionCost = (value, session) =>
  costTotal(value, {
    unpriced: session?.stats?.unpriced_requests,
    requests: session?.stats?.request_count,
    cell: true,
  });

// The row menu, as on the Keys grid. An active session can be closed. An
// idle one reads Closed but opens again on its next request, so it can be
// closed for good. Either way the session is closed by a user, which stays
// closed (Closed for good), so each asks first, naming the session. One a
// user closed has nothing to close. The gateway keeps logging requests that
// send its id under it. Closing writes to the workspace (useWorkspaceWrite):
// for a role that cannot, the menu still says what closing does, and the item
// is disabled and names the roles that can.
const STAYS_CLOSED =
  "It stays closed when a request with this session id arrives. Those requests are still logged under it.";
const CLOSE_ITEM = {
  active: {
    primary: "Close session",
    secondary: STAYS_CLOSED,
    title: (id) => `Close session ${id}?`,
  },
  idle: {
    primary: "Close for good",
    secondary: STAYS_CLOSED,
    title: (id) => `Close session ${id} for good?`,
  },
};

/** The close the row menu offers for a session, or null when none. */
function closeItem(session) {
  if (!session) return null;
  if (session.status === "active") return CLOSE_ITEM.active;
  return session.closed_reason === "idle" ? CLOSE_ITEM.idle : null;
}

function SessionActions({ session, onClose, busy, canWrite, disabledReason }) {
  const [anchor, setAnchor] = useState(null);
  const item = closeItem(session);
  if (!item) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <IconButton
        size="small"
        aria-label={`Actions for session ${session.session_id}`}
        aria-haspopup="menu"
        onClick={(event) => {
          event.stopPropagation();
          setAnchor(event.currentTarget);
        }}
      >
        <FaIcon name="more" />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={(event) => {
          event?.stopPropagation?.();
          setAnchor(null);
        }}
        onClick={(event) => event.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <WhyDisabled reason={canWrite ? null : disabledReason} display="block">
          <MenuItem
            disabled={busy || !canWrite}
            onClick={(event) => {
              event.stopPropagation();
              if (!canWrite) return;
              setAnchor(null);
              onClose(session);
            }}
            sx={{ maxWidth: 320, whiteSpace: "normal" }}
          >
            <ListItemText
              primary={item.primary}
              secondary={item.secondary}
              secondaryTypographyProps={{ typography: "s2" }}
            />
          </MenuItem>
        </WhyDisabled>
      </Menu>
    </Box>
  );
}

SessionActions.propTypes = {
  session: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  canWrite: PropTypes.bool.isRequired,
  disabledReason: PropTypes.string,
};

export default function SessionExplorerSection() {
  const { canWrite, disabledReason } = useWorkspaceWrite();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    status,
    search,
    page,
    session: openId,
  } = readSessionQuery(searchParams);
  const updateQuery = (patch, replace = true) =>
    setSearchParams((current) => updateSessionQuery(current, patch), {
      replace,
    });

  const copySessionLink = async () => {
    const query = updateSessionQuery(searchParams, { session: openId });
    const href = new URL(
      `${location.pathname}?${query.toString()}${location.hash}`,
      window.location.origin,
    ).href;
    try {
      await navigator.clipboard.writeText(href);
      enqueueSnackbar("Session link copied", { variant: "success" });
    } catch {
      enqueueSnackbar("Session link could not be copied", {
        variant: "error",
      });
    }
  };

  const { data, isLoading, error, refetch, isFetching } = useSessions({
    status,
    search: search.trim(),
    page,
  });
  const closeSession = useCloseSession();
  // The session a row menu asked to close, until the user confirms.
  const [closeTarget, setCloseTarget] = useState(null);
  const closeTargetItem = closeItem(closeTarget);

  const sessions = useMemo(() => data?.results ?? [], [data]);
  // Auto-created sessions carry their id as their name, so Name is often
  // empty on every row: it starts hidden then, unless this browser kept the
  // user's own column choice (GatewayGrid stateKey), which wins.
  const nameSaved = useMemo(
    () =>
      (loadColumnState(STATE_KEY) || []).some((col) => col.colId === "name"),
    [],
  );
  const namesEmpty =
    sessions.length > 0 && sessions.every((s) => ownName(s) === "");
  // Every row carries the same timeout (a Django setting).
  const idleTimeout = sessions[0]?.idle_timeout_seconds;
  const total = data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(total / SESSIONS_PAGE_SIZE));
  const filtered = Boolean(status || search.trim());

  const close = useCallback(
    (session) =>
      closeSession.mutate(session.id, {
        onSuccess: () =>
          enqueueSnackbar(`Session ${session.session_id} closed`, {
            variant: "success",
          }),
        onError: (err) => {
          const fallback = `Session ${session.session_id} could not be closed`;
          enqueueSnackbar(getErrorMessage(err, fallback) || fallback, {
            variant: "error",
          });
        },
      }),
    [closeSession],
  );

  // Each minimum is to hold its header: the label, 32 px of padding and,
  // sortable, the 24 px sort arrow (GatewayGrid draws no menu button), and
  // Status its widest chip; together they add up to 1,168 px of the 1,189 px
  // grid at 1440 (sessions/__tests__/SessionsPage.test.jsx). The label widths
  // are computed from Inter's advance widths, not browser-measured.
  const columns = useMemo(
    () => [
      {
        colId: "session",
        headerName: "Session",
        minWidth: 160,
        flex: 1.4,
        valueGetter: ({ data: s }) => s.session_id,
        cellRenderer: ({ value }) => <Clipped value={value} />,
      },
      {
        colId: "name",
        headerName: "Name",
        minWidth: 124,
        flex: 1,
        valueGetter: ({ data: s }) => ownName(s),
        cellRenderer: ({ value }) => <Clipped value={value} secondary />,
        ...(!nameSaved && { hide: namesEmpty }),
      },
      {
        colId: "status",
        headerName: "Status",
        headerTooltip: sessionStatusHelp(idleTimeout),
        // Holds the widest status chip, Closed for good, whole.
        minWidth: 136,
        flex: 0.6,
        valueGetter: ({ data: s }) =>
          sessionStatus(s.status, s.closed_reason).label,
        cellRenderer: ({ data: s }) => {
          const st = sessionStatus(
            s.status,
            s.closed_reason,
            s.idle_timeout_seconds,
          );
          return (
            <Box
              component="span"
              title={st.detail || undefined}
              sx={{ display: "inline-flex", alignItems: "center" }}
            >
              <GatewayStatusChip
                tone={st.tone}
                label={st.label}
                showIcon={false}
              />
            </Box>
          );
        },
      },
      {
        // When the session last saw a request: what Active and an idle close
        // are read from.
        colId: "lastRequest",
        headerName: "Last request",
        minWidth: 156,
        flex: 1,
        valueGetter: ({ data: s }) => s.last_request_at || null,
        cellRenderer: ({ value }) => (
          <Clipped value={value ? formatLogTime(value) : EMPTY} secondary />
        ),
      },
      {
        colId: "requests",
        headerName: "Requests",
        minWidth: 124,
        flex: 0.6,
        valueGetter: ({ data: s }) => s.stats?.request_count ?? 0,
        cellRenderer: ({ value }) => (
          <Clipped value={Number(value).toLocaleString()} />
        ),
      },
      {
        colId: "tokens",
        headerName: "Tokens",
        minWidth: 112,
        flex: 0.6,
        valueGetter: ({ data: s }) => s.stats?.total_tokens ?? 0,
        cellRenderer: ({ value }) => (
          <Clipped value={Number(value).toLocaleString()} />
        ),
      },
      {
        colId: "cost",
        headerName: "Cost",
        minWidth: 120,
        flex: 0.6,
        // No cost recorded is unknown, not free; so is the cost of requests
        // the gateway found no price for (stored at 0): unknown when none
        // had one, a floor when some had none.
        valueGetter: ({ data: s }) => {
          const cost = s.stats?.total_cost;
          if (cost == null || cost === "") return null;
          return sessionCost(cost, s).unknown ? null : Number(cost);
        },
        cellRenderer: ({ data: s }) => {
          // Read from the row: an unknown cost's sort value is null.
          const { text, title } = sessionCost(s.stats?.total_cost, s);
          return <Clipped value={text} title={title ?? undefined} />;
        },
      },
      {
        colId: "latency",
        headerName: "Avg latency",
        minWidth: 140,
        flex: 0.7,
        valueGetter: ({ data: s }) => s.stats?.avg_latency_ms ?? null,
        cellRenderer: ({ value }) => <Clipped value={formatMs(value)} />,
      },
      {
        // When the session started: its first request. created_at is when
        // ingestion stored the row, one time for a whole batch of sessions,
        // so it is read only for a session with no request yet. The API
        // pages by created_at (the gateway sees a session's first request
        // within one ingestion batch), and the rows sort by this. The colId
        // stays "created" so saved layouts keep this column's width and sort.
        colId: "created",
        headerName: "Started",
        minWidth: 156,
        flex: 1,
        sort: "desc",
        valueGetter: ({ data: s }) => sessionStartedAt(s),
        cellRenderer: ({ value }) => (
          <Clipped value={formatLogTime(value)} secondary />
        ),
      },
      {
        colId: "actions",
        headerName: "",
        width: 64,
        minWidth: 64,
        maxWidth: 64,
        flex: 0,
        sortable: false,
        resizable: false,
        valueGetter: ({ data: s }) => s.id,
        cellRenderer: ({ data: s }) => (
          <SessionActions
            session={s}
            onClose={setCloseTarget}
            busy={closeSession.isPending}
            canWrite={canWrite}
            disabledReason={disabledReason}
          />
        ),
      },
    ],
    [
      canWrite,
      closeSession.isPending,
      disabledReason,
      idleTimeout,
      nameSaved,
      namesEmpty,
    ],
  );

  // Filter edits replace the current list entry; opening a row pushes one
  // entry so Back returns to exactly this investigation without its drawer.
  const onFilter = (field) => (value) =>
    updateQuery({ [field]: value, page: 1 });

  return (
    <GatewayPageFrame
      title="Sessions"
      testId="gateway-sessions"
      description="Conversations the gateway grouped by session id, with their requests, tokens, cost and latency."
    >
      <GatewayFilterBar
        search={search}
        onSearchChange={onFilter("search")}
        searchPlaceholder="Search sessions by id or name"
        actions={
          !error && !isLoading ? (
            <Typography typography="s2" color="text.secondary">
              {`${sessions.length.toLocaleString()} of ${total.toLocaleString()} sessions`}
            </Typography>
          ) : null
        }
      >
        <GroupByToggle
          value={status}
          options={STATUS_OPTIONS}
          onChange={onFilter("status")}
          label="Status"
        />
      </GatewayFilterBar>

      {error ? (
        <GatewayEmptyState
          icon="alertCircle"
          title="Sessions could not be loaded"
          description={error.message || "The gateway API did not answer."}
          actionLabel="Retry"
          onAction={() => refetch()}
        />
      ) : (
        <GatewayGrid
          rowData={isLoading ? undefined : sessions}
          loading={isLoading}
          columnDefs={columns}
          stateKey={STATE_KEY}
          getRowId={(params) => params.data.id}
          onRowClicked={(event) => {
            if (event.event?.target?.closest?.("button")) return;
            updateQuery({ session: event.data.id }, false);
          }}
          rowStyle={{ cursor: "pointer" }}
          rowHeight={36}
          emptyState={
            filtered
              ? {
                  variant: "no-results",
                  title: "No matching sessions",
                  description: "No session matches this status and search.",
                  actionLabel: "Clear filters",
                  onAction: () =>
                    updateQuery({ status: "", search: "", page: 1 }),
                }
              : {
                  icon: "sessions",
                  title: "No sessions yet",
                  description:
                    "Requests that send an x-agentcc-session-id header are grouped here, one row per conversation.",
                }
          }
        />
      )}

      {!error && pages > 1 && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          justifyContent="flex-end"
        >
          <Typography typography="s2" color="text.secondary">
            {`Page ${page} of ${pages}`}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            disabled={page <= 1 || isFetching}
            onClick={() => updateQuery({ page: page - 1 })}
          >
            Previous
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={page >= pages || isFetching}
            onClick={() => updateQuery({ page: page + 1 })}
          >
            Next
          </Button>
        </Stack>
      )}

      <SessionDetailDrawer
        sessionId={openId}
        open={Boolean(openId)}
        onClose={() => updateQuery({ session: null })}
        onCopyLink={copySessionLink}
      />

      <ConfirmDialog
        open={Boolean(closeTargetItem)}
        onClose={() => setCloseTarget(null)}
        title={
          closeTargetItem
            ? closeTargetItem.title(closeTarget.session_id)
            : "Close session"
        }
        message={`This is permanent. ${STAYS_CLOSED}`}
        confirmLabel={closeTargetItem?.primary || "Close session"}
        confirmColor="error"
        isLoading={closeSession.isPending}
        onConfirm={() => {
          const target = closeTarget;
          setCloseTarget(null);
          close(target);
        }}
      />
    </GatewayPageFrame>
  );
}
