import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import {
  COMPAT_STATUS,
  affectsLabel,
  capabilityAnswerOf,
  waitingWordOf,
} from "../_mock/compatibility";
import CapabilityStatus from "./CapabilityStatus";

const MONO = "ui-monospace, Menlo, monospace";
/* Status · capability · reason · affected · checked · action */
const COLS = "104px 184px minmax(0, 1fr) 152px 64px 140px";

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
const day = (iso) =>
  iso
    ? new Date(iso).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : null;

/* The answer's icon: what state it's in, before reading a word. */
const ANSWER_ICON = {
  checking: { icon: "svg-spinners:ring-resize", color: "text.secondary" },
  unchecked: { icon: "solar:clock-circle-linear", color: "text.tertiary" },
  blocked: { icon: "solar:danger-circle-bold", color: "status.danger.fg" },
  stale: { icon: "solar:danger-triangle-bold", color: "status.warning.fg" },
  ready: { icon: "solar:check-circle-bold", color: "status.success.fg" },
};
const STEP_ICON = {
  done: { icon: "solar:check-circle-linear", color: "text.tertiary" },
  running: { icon: "svg-spinners:ring-resize", color: "text.secondary" },
  waiting: { icon: "solar:clock-circle-linear", color: "text.disabled" },
};
const FRAME = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};

/* A row the connection check decides (or would, once it runs). */
const fromCheck = (row) =>
  !!row.checkId ||
  !!row.blockedBy ||
  row.status === "untested" ||
  row.status === "stale";

/* One labelled line of the opened row: reason, fix, evidence. */
function Line({ k, children }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="baseline">
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 96,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}>
        {children}
      </Box>
    </Stack>
  );
}
Line.propTypes = { k: PropTypes.string.isRequired, children: PropTypes.node };

/* One capability (TH-8877): its status chip, the reason, the cases it
   affects, when it was last checked and its fix — opened, the affected
   cases, the evidence and the reason with a Fix that opens the repair. */
/* The statuses that always report their affected count (TH-8877). */
const COUNTED = ["blocked", "partial"];

function CapabilityRow({ row, requested, titles, testing, primary, onRepair }) {
  const [open, setOpen] = useState(false);
  const n = row.affected.length;
  const opens = n > 0 || !!row.evidenceSteps;
  const checking = testing && fromCheck(row);
  const action = (() => {
    if (testing) return null;
    if (row.repair === "connection" && onRepair)
      return { label: "Fix", onClick: () => onRepair(row) };
    if (row.repair === "agent" && onRepair)
      return { label: "Add the tools", onClick: () => onRepair(row) };
    if (row.repair === "release" && onRepair)
      return { label: "Run release checks", onClick: () => onRepair(row) };
    if (row.repair === "rebuild" && onRepair)
      return { label: "Rebuild environment", onClick: () => onRepair(row) };
    if (row.repair === "testcall" && onRepair)
      return { label: "Make a test call", onClick: () => onRepair(row) };
    return null;
  })();
  return (
    <Box sx={{ "& + &": { borderTop: "1px solid", borderColor: "divider" } }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: COLS,
          columnGap: 2,
          alignItems: "start",
          px: 1.5,
          py: 1,
        }}
      >
        <CapabilityStatus status={row.status} checking={checking} />
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {row.label}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
        >
          {row.detail}
          {row.fixedAt && (
            <Box component="span" sx={{ color: "text.tertiary" }}>
              {` · Fixed ${day(row.fixedAt)}, was Blocked`}
            </Box>
          )}
        </Typography>
        {/* "4 of 10 cases affected" — click it for the cases and evidence. */}
        {n ? (
          <Box
            {...clickable(() => setOpen((o) => !o), { expanded: open })}
            sx={{
              ...focusRing,
              cursor: "pointer",
              typography: "s2",
              textAlign: "right",
              fontVariantNumeric: "tabular-nums",
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              gap: 0.5,
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {`${affectsLabel(n, requested)} affected`}
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={12}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
          </Box>
        ) : opens ? (
          <Box
            {...clickable(() => setOpen((o) => !o), { expanded: open })}
            sx={{
              ...focusRing,
              cursor: "pointer",
              typography: "s2",
              color: "text.secondary",
              textAlign: "right",
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {/* A fixed row proves its fix: none of the cases it held back. */}
            {row.fixedAt
              ? `${affectsLabel(0, requested)} affected`
              : "Evidence"}
          </Box>
        ) : (
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              textAlign: "right",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {/* Blocked and Partial always say how many cases they touch. */}
            {COUNTED.includes(row.status)
              ? `${affectsLabel(0, requested)} affected`
              : "—"}
          </Typography>
        )}
        {/* When it was last checked. */}
        <Typography
          title={
            row.checkedAt
              ? row.fromBuild
                ? `From the environment build, ${when(row.checkedAt)}`
                : `Checked ${when(row.checkedAt)}`
              : fromCheck(row)
                ? "No check has covered this yet"
                : "Read from how it's connected — no check needed"
          }
          sx={{
            typography: "s2",
            color: row.checkedAt ? "text.secondary" : "text.disabled",
            textAlign: "right",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {row.checkedAt ? day(row.checkedAt) : fromCheck(row) ? "Never" : "—"}
        </Typography>
        <Box sx={{ textAlign: "right" }}>
          {action && (
            <Button
              size="small"
              variant={primary ? "contained" : "outlined"}
              color={primary ? "primary" : "inherit"}
              onClick={action.onClick}
              sx={
                primary ? {} : { color: "text.primary", borderColor: "divider" }
              }
            >
              {action.label}
            </Button>
          )}
        </Box>
      </Box>
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={0.75}
          sx={{ px: 1.5, pb: 1.5, pl: "calc(104px + 28px)" }}
        >
          <Line k="Reason">{row.detail}</Line>
          {row.fix?.would && <Line k="Fixed when">{row.fix.would}</Line>}
          {row.evidenceSteps && (
            <Line k="Evidence">
              <Box
                sx={{
                  ...FRAME,
                  bgcolor: "action.hover",
                  px: 1.25,
                  py: 0.75,
                }}
              >
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
                >
                  {`Connection check, ${when(row.checkedAt)}`}
                </Typography>
                {row.evidenceSteps.map((s) => (
                  <Stack
                    key={s.k}
                    direction="row"
                    spacing={1.5}
                    alignItems="baseline"
                  >
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        width: 84,
                        flexShrink: 0,
                      }}
                    >
                      {s.k}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontFamily: MONO,
                        color: "text.primary",
                        minWidth: 0,
                        wordBreak: "break-word",
                      }}
                    >
                      {s.v}
                    </Typography>
                  </Stack>
                ))}
              </Box>
            </Line>
          )}
          {n > 0 && (
            <Line k="Affected">
              {row.affected.map((id) => (
                <Typography
                  key={id}
                  noWrap
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {titles[id] || id}
                </Typography>
              ))}
            </Line>
          )}
          {action && (
            <Box sx={{ pl: "108px" }}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={action.onClick}
              >
                {/* Named for the repair screen it opens (TH-8877). */}
                {action.label === "Fix" && row.fix?.type
                  ? `Fix ${row.fix.type.toLowerCase()}`
                  : action.label}
              </Button>
            </Box>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
CapabilityRow.propTypes = {
  /* This row's action is the next thing to do on the page. */
  primary: PropTypes.bool,
  row: PropTypes.object.isRequired,
  requested: PropTypes.number.isRequired,
  titles: PropTypes.object,
  testing: PropTypes.bool,
  onRepair: PropTypes.func,
};

/**
 * The Capabilities panel for one agent on one environment (TH-8877): each
 * capability with its status chip, reason, the cases it affects, when it was
 * last checked and its fix — the ones that need something first. Re-check at
 * the top runs the connection check again; until one has run, every row the
 * check decides reads Untested, never Supported.
 */
export default function CapabilitiesPanel({
  report,
  agentName,
  worldName,
  titles = {},
  checkable = false,
  testing = false,
  checkSteps = null,
  onCheck,
  onRepair,
}) {
  const answer = capabilityAnswerOf(report, { checkable, testing });
  const icon = ANSWER_ICON[answer.kind];
  const requested = report.scope.requested;
  const rows = [...report.rows].sort(
    (a, b) => COMPAT_STATUS[b.status].rank - COMPAT_STATUS[a.status].rank,
  );
  /* Re-check is the next thing to do when there's nothing checked, a fix to
     confirm, or a check that's out of date. */
  const recheckFirst = ["unchecked", "stale"].includes(answer.kind);
  /* Otherwise the next thing to do is the top blocked row's fix. */
  const primaryId = recheckFirst
    ? null
    : rows.find((r) => r.status === "blocked" && r.repair)?.id;
  const neverChecked = !report.testedAt && !report.checkedBeforeAt;
  return (
    <Stack spacing={2}>
      <Box sx={{ ...FRAME, px: 2, py: 1.5 }}>
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <Iconify
            icon={icon.icon}
            width={20}
            sx={{ color: icon.color, flexShrink: 0, mt: "2px" }}
          />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`Can ${agentName} ${report.identity.agentVersion} be tested in ${worldName} ${report.identity.worldVersion}?`}
            </Typography>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {answer.title}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {answer.body}
            </Typography>
            {/* The check, step by step, while it runs. */}
            {checkSteps && (
              <Stack spacing={0.5} sx={{ mt: 1 }}>
                {checkSteps.map((s) => (
                  <Stack
                    key={s.label}
                    direction="row"
                    spacing={1}
                    alignItems="center"
                  >
                    <Iconify
                      icon={STEP_ICON[s.state].icon}
                      width={14}
                      sx={{ color: STEP_ICON[s.state].color }}
                    />
                    <Typography
                      sx={{
                        typography: "s2",
                        color:
                          s.state === "waiting"
                            ? "text.disabled"
                            : "text.secondary",
                      }}
                    >
                      {s.label}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                mt: 0.5,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {[
                report.testedAt
                  ? `Last checked ${when(report.testedAt)}`
                  : report.checkedBeforeAt
                    ? `Last checked ${when(report.checkedBeforeAt)}, before the fix`
                    : checkable
                      ? "Not checked yet"
                      : null,
                `${report.scope.eligible} eligible · ${report.scope.blocked.length} blocked · ${report.scope.untested.length} ${waitingWordOf(report)} of ${requested}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Box>
          {checkable && onCheck && (
            <Button
              size="small"
              variant={recheckFirst ? "contained" : "outlined"}
              color={recheckFirst ? "primary" : "inherit"}
              disabled={testing}
              onClick={onCheck}
              startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
              sx={{
                flexShrink: 0,
                ...(!recheckFirst && {
                  color: "text.primary",
                  borderColor: "divider",
                }),
              }}
            >
              {testing
                ? "Checking…"
                : neverChecked
                  ? "Run a check"
                  : "Re-check"}
            </Button>
          )}
        </Stack>
      </Box>

      <Box sx={{ ...FRAME, overflowX: "auto" }}>
        <Box sx={{ minWidth: 880 }}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: COLS,
              columnGap: 2,
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {["Status", "Capability", "Reason", "Affected", "Checked", ""].map(
              (h, i) => (
                <Typography
                  key={h || i}
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    textAlign: i >= 3 ? "right" : "left",
                  }}
                >
                  {h}
                </Typography>
              ),
            )}
          </Box>
          {rows.map((r) => (
            <CapabilityRow
              key={r.id}
              row={r}
              requested={requested}
              titles={titles}
              testing={testing}
              primary={r.id === primaryId}
              onRepair={onRepair}
            />
          ))}
        </Box>
      </Box>
    </Stack>
  );
}
CapabilitiesPanel.propTypes = {
  report: PropTypes.object.isRequired,
  agentName: PropTypes.string.isRequired,
  worldName: PropTypes.string.isRequired,
  /* Scenario names by id, for the affected cases. */
  titles: PropTypes.object,
  /* The agent has a connection check to run. */
  checkable: PropTypes.bool,
  testing: PropTypes.bool,
  /* The check's steps while it runs: [{ label, state }]. */
  checkSteps: PropTypes.array,
  onCheck: PropTypes.func,
  onRepair: PropTypes.func,
};
