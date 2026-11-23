import PropTypes from "prop-types";
import {
  Box,
  Button,
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
import { isAgentFix } from "./readinessView";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";
import { lineSx, spinner } from "./readinessStyles";

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
function WaitsOn({ envId, picks, keys, line, report }) {
  const cases = useReadinessCases(envId, {
    picks,
    keys,
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
  line: PropTypes.object.isRequired,
  report: PropTypes.string,
};

// One area that isn't ready: its reason on one line, and what we saw, the fix and its action once opened.
export default function ReadinessLine({
  line,
  envId,
  picks,
  keys,
  report,
  open,
  onToggle,
  checking,
  inFlight,
  onRecheck,
  onRetryAgent,
  onOpenAgentVersions,
}) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { fix } = line;
  const label = COPY.areas[line.area] || line.area;
  // Checking the pair again is this line's whole fix: it opens on what it checks and why, not "check again".
  const recheckOnly = fix?.type === "recheck" && fix.recheck?.scope === "pair";
  const agentFix = isAgentFix(fix) && Boolean(onOpenAgentVersions);
  const bodyId = `readiness-fix-${line.area}`;
  return (
    // While a check runs these are the last results: dimmed, nothing to press.
    <Box sx={{ ...lineSx, opacity: checking && !inFlight ? 0.5 : 1 }}>
      <Stack direction="row" spacing={1} alignItems="center">
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
        {fix && !checking && (
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
      {fix && (
        <Collapse
          id={bodyId}
          in={open && (!checking || inFlight)}
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
                  line={line}
                  report={report}
                />
              </Box>
            )}
            {(agentFix || fix.recheck) && (
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
                {fix.recheck && (
                  <Button
                    size="small"
                    variant={agentFix ? "outlined" : "contained"}
                    disabled={checking}
                    startIcon={inFlight ? spinner : null}
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
};
