import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import CustomTooltip from "src/components/tooltip";
import { useReadinessCases } from "src/api/simulate-environments/readiness";
import { fToNow } from "src/utils/format-time";
import ChatMarkdown from "../../../buildEnvironment/console/ChatMarkdown";
import ViewerLock from "../../../components/ViewerLock";
import { WORKSPACE_COPY } from "../../workspace.constants";
import { isAgentFix } from "./readinessView";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";
import Spinner from "./Spinner";
import { lineSx } from "./readinessStyles";

const gridSx = {
  display: "grid",
  gridTemplateColumns: "96px minmax(0, 1fr)",
  columnGap: 1.5,
  rowGap: 0.75,
  typography: "s3",
};

function GridRow({ label, children }) {
  return (
    <>
      <Box sx={{ color: "text.secondary" }}>{label}</Box>
      <Box sx={{ color: "text.primary", minWidth: 0 }}>{children}</Box>
    </>
  );
}

GridRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

const WAITS_NAMES = 3;

// The scenarios this line's row holds back, by name; nothing until some load.
function WaitsOn({ envId, picks, keys, trials, line, report }) {
  const cases = useReadinessCases(envId, {
    picks,
    keys,
    trials,
    row: line.rowId,
    report,
    enabled: Boolean(line.rowId) && line.affected > 0,
  });
  const names = (cases.data || []).map((one) => one.name || one.key);
  if (!names.length) return null;
  const shown = names.slice(0, WAITS_NAMES);
  const more = Math.max(line.affected, names.length) - shown.length;
  return (
    <GridRow label={COPY.grid.waits}>
      {shown.join(", ")}
      {more > 0 && ` ${COPY.namesMore(more)}`}
    </GridRow>
  );
}

WaitsOn.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  trials: PropTypes.number,
  line: PropTypes.object.isRequired,
  report: PropTypes.string,
};

// A fix run's state for a line; a line no fix took on has none.
const PROGRESS_ROLE = {
  queued: "neutral",
  fixing: "info",
  waiting_on_you: "warning",
  waiting_on_other: "neutral",
  fixed: "success",
  could_not_fix: "danger",
};

// How the builder chat's fix of a line stands, or where an earlier one saved its changes.
function FixState({ state, line }) {
  if (state === "fixing") {
    return (
      <Stack
        direction="row"
        spacing={0.5}
        alignItems="center"
        sx={{ color: "primary.main", flexShrink: 0 }}
      >
        <Spinner />
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightMedium" }}>
          {COPY.progress.fixing}
        </Typography>
      </Stack>
    );
  }
  if (state) {
    const text = COPY.progress[state];
    return (
      <StatusBadge
        status={state}
        role={PROGRESS_ROLE[state]}
        label={
          typeof text === "function"
            ? text(COPY.areas[line.waitingOnArea])
            : text
        }
        size="sm"
      />
    );
  }
  const { result } = line;
  if (result?.state !== "fixed" || !(result.environment || result.suite))
    return null;
  return (
    <StatusBadge
      status="fixed"
      role="success"
      label={COPY.fixedIn(result)}
      size="sm"
    />
  );
}

FixState.propTypes = {
  state: PropTypes.oneOf(Object.keys(PROGRESS_ROLE)),
  line: PropTypes.object.isRequired,
};

// One area that isn't ready: its reason on one line, and what we saw, the fix and its action once opened.
export default function ReadinessLine({
  line,
  envId,
  picks,
  keys,
  trials,
  report,
  open,
  onToggle,
  checking,
  inFlight,
  onRecheck,
  onRetryAgent,
  onOpenAgentVersions,
  chatFix = null,
  credentials = null,
  saved = null,
}) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { fix } = line;
  const label = COPY.areas[line.area] || line.area;
  // Checking the pair again is this line's whole fix: it opens on what it checks and why, not "check again".
  const recheckOnly = fix?.type === "recheck" && fix.recheck?.scope === "pair";
  // Missing values are asked for here; any other fix of the agent is made on its versions.
  const agentFix =
    isAgentFix(fix) && Boolean(onOpenAgentVersions) && !credentials;
  const bodyId = `readiness-fix-${line.area}`;
  const state = PROGRESS_ROLE[line.progress?.state]
    ? line.progress.state
    : null;
  // While the chat works on a line, its state stands in for the details.
  const opens = Boolean(fix) && (!state || state === "could_not_fix");
  const why = line.progress?.reason?.message;
  const showWhy =
    why && (state === "could_not_fix" || state === "waiting_on_you");
  // The builder chat's re-check follows its fix.
  const rechecks = !chatFix && fix?.recheck && (!credentials || inFlight);
  return (
    // While a check runs these are the last results: dimmed, nothing to press. A fixed line is already the new one.
    <Box
      sx={{
        ...lineSx,
        opacity: checking && !inFlight && state !== "fixed" ? 0.5 : 1,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center">
        {chatFix && (
          // Lines already being fixed keep the space, so names line up.
          <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
            {chatFix.onSelect && (
              <Checkbox
                size="small"
                checked={chatFix.selected}
                disabled={chatFix.locked}
                onChange={(event) => chatFix.onSelect(event.target.checked)}
                inputProps={{ "aria-label": COPY.selectLine(label) }}
                sx={{ p: 0 }}
              />
            )}
          </Box>
        )}
        <Box sx={{ flexShrink: 0 }}>
          <StatusBadge status={line.status} label={label} variant="dot" />
        </Box>
        <CustomTooltip
          show={Boolean(line.reason)}
          title={
            <Box sx={{ whiteSpace: "pre-line" }}>
              {[line.reason, line.consequence].filter(Boolean).join("\n\n")}
            </Box>
          }
          size="small"
          arrow
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {line.reason}
          </Typography>
        </CustomTooltip>
        {line.affected > 0 && (
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.secondary", flexShrink: 0 }}
          >
            {line.status === "blocked"
              ? COPY.blockedCount(line.affected)
              : COPY.affected(line.affected)}
          </Typography>
        )}
        <FixState state={state} line={line} />
        {opens && !checking && (
          <IconButton
            size="small"
            onClick={onToggle}
            aria-label={COPY.details(label)}
            aria-expanded={open}
            aria-controls={bodyId}
            sx={{ flexShrink: 0, p: 0.25 }}
          >
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={16}
              sx={{
                transition: (theme) => theme.transitions.create("transform"),
                transform: open ? "rotate(180deg)" : "none",
                "@media (prefers-reduced-motion: reduce)": {
                  transition: "none",
                },
              }}
            />
          </IconButton>
        )}
      </Stack>
      {showWhy && (
        // The builder's own reply, often markdown.
        <ChatMarkdown text={why} dim small sx={{ pt: 0.5 }} />
      )}
      {fix && (
        <Collapse
          id={bodyId}
          in={open && opens && (!checking || inFlight)}
          timeout={reduceMotion ? 0 : "auto"}
          unmountOnExit
        >
          <Stack spacing={1} sx={{ pt: 1, pl: 2.5 }}>
            {recheckOnly ? (
              <Box sx={gridSx}>
                {COPY.checks[line.area] && (
                  <GridRow label={COPY.grid.checks}>
                    {COPY.checks[line.area]}
                  </GridRow>
                )}
                {line.checkedAt && (
                  <GridRow label={COPY.grid.lastCheck}>
                    {fToNow(line.checkedAt)}
                  </GridRow>
                )}
                <GridRow label={COPY.grid.why}>
                  {line.reason}
                  {line.consequence && (
                    <Box sx={{ color: "text.secondary", pt: 0.5 }}>
                      {line.consequence}
                    </Box>
                  )}
                </GridRow>
              </Box>
            ) : (
              <Box sx={gridSx}>
                {line.reason && (
                  <GridRow label={COPY.grid.saw}>{line.reason}</GridRow>
                )}
                {fix.action && (
                  <GridRow label={COPY.grid.fix}>{fix.action}</GridRow>
                )}
                {fix.would && (
                  <GridRow label={COPY.grid.would}>{fix.would}</GridRow>
                )}
                <WaitsOn
                  envId={envId}
                  picks={picks}
                  keys={keys}
                  trials={trials}
                  line={line}
                  report={report}
                />
              </Box>
            )}
            {saved}
            {!inFlight && credentials}
            {chatFix && fix.message && (
              <Box>
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", mb: 0.5 }}
                >
                  {COPY.sendsToChat}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.primary",
                    bgcolor: "background.neutral",
                    borderRadius: 1,
                    px: 1.25,
                    py: 1,
                  }}
                >
                  {fix.message}
                </Typography>
              </Box>
            )}
            {chatFix?.onFixOne && (
              <Box>
                <ViewerLock
                  locked={chatFix.locked}
                  title={WORKSPACE_COPY.viewer.chat}
                >
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={checking || chatFix.locked}
                    onClick={chatFix.onFixOne}
                  >
                    {chatFix.fixOneLabel}
                  </Button>
                </ViewerLock>
              </Box>
            )}
            {(agentFix || rechecks) && (
              <Stack direction="row" spacing={1}>
                {agentFix && (
                  <Button
                    size="small"
                    variant="contained"
                    onClick={onOpenAgentVersions}
                  >
                    {COPY.openAgentVersions}
                  </Button>
                )}
                {rechecks && (
                  <Button
                    size="small"
                    variant={agentFix ? "outlined" : "contained"}
                    disabled={checking}
                    startIcon={inFlight ? <Spinner /> : null}
                    onClick={
                      fix.recheck.scope === "agent" ? onRetryAgent : onRecheck
                    }
                  >
                    {inFlight && COPY.checking}
                    {!inFlight &&
                      (fix.recheck.scope === "agent"
                        ? COPY.retryAgent
                        : COPY.recheckPair)}
                  </Button>
                )}
              </Stack>
            )}
          </Stack>
        </Collapse>
      )}
    </Box>
  );
}

ReadinessLine.propTypes = {
  line: PropTypes.object.isRequired,
  envId: PropTypes.string,
  // What the opened line's scenario names are read for.
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  trials: PropTypes.number,
  report: PropTypes.string,
  open: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  checking: PropTypes.bool,
  // This line's own re-check is the one running.
  inFlight: PropTypes.bool,
  onRecheck: PropTypes.func.isRequired,
  onRetryAgent: PropTypes.func.isRequired,
  // Opens the agent's versions, where a new version or new credentials are made; absent where that can't open.
  onOpenAgentVersions: PropTypes.func,
  // On a line the builder chat can fix: its tick while the chat can take it on, and sending it alone.
  chatFix: PropTypes.shape({
    selected: PropTypes.bool,
    onSelect: PropTypes.func,
    onFixOne: PropTypes.func,
    fixOneLabel: PropTypes.string,
    locked: PropTypes.bool,
  }),
  // The form for the agent's missing values, and the version they were saved as.
  credentials: PropTypes.node,
  saved: PropTypes.node,
};
