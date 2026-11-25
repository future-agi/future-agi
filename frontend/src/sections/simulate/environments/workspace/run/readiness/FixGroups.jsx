import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Checkbox, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ErrorState from "src/components/error-state";
import {
  fixIsActive,
  useFixRun,
  useStartGroupFix,
} from "src/api/simulate-environments/readinessFix";
import ViewerLock from "../../../components/ViewerLock";
import useSimulationAccess from "../../../hooks/useSimulationAccess";
import { WORKSPACE_COPY } from "../../workspace.constants";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";
import Spinner from "./Spinner";
import { boxSx, lineSx } from "./readinessStyles";

const GROUP_ICON = {
  needs_you: "solar:user-linear",
  builder: "solar:chat-round-dots-linear",
  recheck: "solar:refresh-linear",
};

// Lines the builder chat can take on, as the server says.
const fixable = (line) => Boolean(line.action?.available);

// One group: its own bordered card, a tinted header and its lines inside.
function GroupCard({ group, count, lead, action, note, children }) {
  return (
    <Box
      component="section"
      aria-labelledby={`readiness-group-${group}`}
      sx={{ ...boxSx, overflow: "hidden" }}
    >
      <Box sx={{ bgcolor: "background.neutral", px: 1.5, py: 0.75 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
            {lead ?? (
              <Iconify
                icon={GROUP_ICON[group]}
                width={16}
                sx={{ color: "text.secondary" }}
              />
            )}
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              id={`readiness-group-${group}`}
              component="h3"
              sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
            >
              {COPY.groups[group]}
              <Box
                component="span"
                sx={{
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                }}
              >
                {` · ${count}`}
              </Box>
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {COPY.groupHints[group]}
            </Typography>
          </Box>
          {action}
        </Stack>
        {note}
      </Box>
      {children}
    </Box>
  );
}

GroupCard.propTypes = {
  group: PropTypes.oneOf(["needs_you", "builder", "recheck"]).isRequired,
  count: PropTypes.number.isRequired,
  // Stands in for the group's icon, e.g. a select-all checkbox.
  lead: PropTypes.node,
  // On the right of the header, e.g. the send button.
  action: PropTypes.node,
  // Under the header, e.g. why sending is held.
  note: PropTypes.node,
  children: PropTypes.node,
};

/**
 * Ready to run's open lines by who fixes them, shown while the server offers Fix. The builder chat's lines are ticked
 * and sent to it; which it can take on, and why not now, is the server's word.
 */
export default function FixGroups({
  envId,
  picks,
  groups,
  checking,
  renderLine,
  onRecheck,
  onGoToChat,
}) {
  const { canEdit } = useSimulationAccess();
  const fixQuery = useFixRun(envId);
  const fixRun = fixQuery.data ?? null;
  // Unread is not "no fix": Fix waits until the run is known.
  const fixUnknown = fixQuery.isError && fixQuery.data === undefined;
  const active = fixIsActive(fixRun);
  const start = useStartGroupFix(envId);
  // A refusal belongs to the run state it met; once the run moves on, drop it.
  const fixState = fixRun?.state;
  const resetStart = start.reset;
  useEffect(() => {
    resetStart();
  }, [fixState, resetStart]);
  // Ticks the user changed, by row id. Before a fix every line starts ticked; while one runs, lines are added on
  // purpose.
  const [ticks, setTicks] = useState(() => new Map());
  const picksKey = JSON.stringify(picks || {});
  useEffect(() => setTicks(new Map()), [picksKey]);
  const ticked = (line) => ticks.get(line.rowId) ?? !active;

  const { builder } = groups;
  const takeable = builder.filter(fixable);
  const chosen = takeable.filter(ticked);
  const held = takeable.find((line) => line.action.held)?.action.held;
  let heldText = null;
  if (fixUnknown) heldText = COPY.fixLoadError;
  // Saving or re-checking: the run takes no more lines until it ends.
  else if (held === "fix_running" && active && !fixRun.accepts_rows)
    heldText = COPY.fixFinishing;
  else if (held) heldText = COPY.fixRefused[held] ?? COPY.fixRefused.default;

  const sending = useRef(false);
  const send = (lines) => {
    // A second click before the first POST lands would send it twice.
    if (sending.current) return;
    sending.current = true;
    start.mutate(
      { group: "builder", rows: lines.map((line) => line.rowId), picks },
      {
        onSuccess: () => setTicks(new Map()),
        onSettled: () => {
          sending.current = false;
        },
      },
    );
  };
  const refusal =
    start.isError &&
    (COPY.fixRefused[start.error?.error] ??
      start.error?.message ??
      COPY.fixRefused.default);

  const builderLine = (line) => {
    const takes = fixable(line);
    return renderLine(line, {
      locked: !canEdit,
      selected: ticked(line),
      onSelect: takes
        ? (on) => setTicks((all) => new Map(all).set(line.rowId, on))
        : undefined,
      onFixOne: takes && !heldText ? () => send([line]) : undefined,
      fixOneLabel: active ? COPY.addOne : COPY.fixOne,
    });
  };
  const showHeld = takeable.length > 0 && heldText && !fixUnknown;

  // Nothing to fix leaves no group box, not an empty padded one.
  if (!Object.values(groups).some((lines) => lines.length)) return null;
  return (
    <Stack
      spacing={1}
      sx={{
        px: 1.5,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {groups.needs_you.length > 0 && (
        <GroupCard group="needs_you" count={groups.needs_you.length}>
          {groups.needs_you.map((line) => renderLine(line))}
        </GroupCard>
      )}
      {builder.length > 0 && (
        <GroupCard
          group="builder"
          count={builder.length}
          lead={
            takeable.length > 0 ? (
              <Checkbox
                size="small"
                checked={chosen.length === takeable.length}
                indeterminate={
                  chosen.length > 0 && chosen.length < takeable.length
                }
                disabled={!canEdit}
                onChange={(event) =>
                  setTicks((all) => {
                    const next = new Map(all);
                    takeable.forEach((line) =>
                      next.set(line.rowId, event.target.checked),
                    );
                    return next;
                  })
                }
                inputProps={{ "aria-label": COPY.selectAll }}
                sx={{ p: 0 }}
              />
            ) : null
          }
          action={
            takeable.length > 0 || active ? (
              <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.chat}>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={
                    !canEdit ||
                    !chosen.length ||
                    checking ||
                    start.isPending ||
                    Boolean(heldText)
                  }
                  aria-describedby={showHeld ? "fix-chat-held" : undefined}
                  onClick={() => send(chosen)}
                  sx={{ py: 0.25, flexShrink: 0 }}
                >
                  {active ? COPY.addSelected(chosen.length) : COPY.sendSelected}
                </Button>
              </ViewerLock>
            ) : null
          }
          note={
            <>
              {fixUnknown && (
                <ErrorState
                  title={COPY.fixLoadError}
                  onRetry={() => fixQuery.refetch()}
                />
              )}
              {showHeld && (
                <Typography
                  id="fix-chat-held"
                  sx={{ typography: "s3", color: "text.secondary" }}
                >
                  {heldText}
                </Typography>
              )}
              {refusal && (
                <Typography
                  role="alert"
                  sx={{ typography: "s3", color: "status.danger.fg" }}
                >
                  {refusal}
                </Typography>
              )}
            </>
          }
        >
          {builder.map(builderLine)}
          {active && (
            <Stack
              data-testid="fix-chat-note"
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ ...lineSx, bgcolor: "background.neutral" }}
            >
              <Typography
                sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
              >
                {COPY.chatFixing(
                  builder
                    .filter((line) => fixRun.rows?.includes(line.rowId))
                    .map((line) => COPY.areas[line.area] || line.area),
                )}
                {!onGoToChat && ` · ${COPY.chatFollow}`}
              </Typography>
              {onGoToChat && (
                <Button size="small" color="inherit" onClick={onGoToChat}>
                  {COPY.goToChat}
                </Button>
              )}
            </Stack>
          )}
        </GroupCard>
      )}
      {groups.recheck.length > 0 && (
        <GroupCard
          group="recheck"
          count={groups.recheck.length}
          action={
            <Button
              size="small"
              variant="outlined"
              disabled={checking}
              startIcon={checking ? <Spinner /> : null}
              onClick={onRecheck}
              sx={{ py: 0.25, flexShrink: 0 }}
            >
              {checking ? COPY.checking : COPY.recheck}
            </Button>
          }
        >
          {groups.recheck.map((line) => renderLine(line))}
        </GroupCard>
      )}
    </Stack>
  );
}

FixGroups.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  // readinessView's groups.
  groups: PropTypes.shape({
    needs_you: PropTypes.array.isRequired,
    builder: PropTypes.array.isRequired,
    recheck: PropTypes.array.isRequired,
  }).isRequired,
  checking: PropTypes.bool,
  // (line, chatFix) -> the line; chatFix for the builder chat's lines.
  renderLine: PropTypes.func.isRequired,
  onRecheck: PropTypes.func.isRequired,
  // Set where the page has no builder chat to follow the fix in.
  onGoToChat: PropTypes.func,
};
