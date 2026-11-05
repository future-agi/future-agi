import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { TextLink } from "../agents/connect/connectBits";
import {
  affectsLabel,
  COMPAT_STATUS,
  compatSummaryLine,
} from "../_mock/compatibility";
import CapabilityStatus from "./CapabilityStatus";

/* Name · status · affected; the reason reads underneath, full width. */
const COLS = "minmax(0, 1fr) 104px 152px";

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;

/**
 * The capability report, compact (TH-8877) — the same capabilities and chips
 * as the agent's Capabilities tab, in the run dialog above the scenario
 * picker and on a finished run's header. Takes a live report's rows or the
 * copy a run kept: what isn't Supported is listed, the rest on request.
 */
export default function CapabilitySummary({
  rows,
  requested,
  testedAt,
  title = "Capabilities",
  onFix,
  onCheck,
  checking = false,
  inlineFixOf,
  onInlineFix,
  children,
}) {
  const [all, setAll] = useState(false);
  /* The row whose Fix is open inline, and the value being typed. */
  const [fixing, setFixing] = useState(null);
  const [fixValue, setFixValue] = useState("");
  const counts = Object.fromEntries(
    rows.map((r) => [
      r.status,
      rows.filter((x) => x.status === r.status).length,
    ]),
  );
  /* The same order as the Capabilities tab: what needs something first. */
  const sorted = [...rows].sort(
    (a, b) =>
      (COMPAT_STATUS[b.status]?.rank || 0) -
      (COMPAT_STATUS[a.status]?.rank || 0),
  );
  const flagged = sorted.filter((r) => r.status !== "supported");
  const n = (r) =>
    Array.isArray(r.affected) ? r.affected.length : r.affected || 0;
  /*
    Short view: capabilities that wait on the same thing read as one row —
    never checked, or a fix applied since the last check — with the one
    check that answers them; what affects no case here folds into one line.
    Every capability is still named, and Show all opens the full report.
  */
  const GROUPS = [
    {
      status: "untested",
      title: (k) => `${k} not tested yet`,
      why: "no connection check has covered them yet.",
    },
    {
      status: "partial",
      title: (k) => `${k} work only in part here`,
      why: "Show all says what each one is missing.",
    },
    {
      status: "stale",
      title: (k) => `${k} need a re-check`,
      why: "a fix was applied since the last check, so check again to confirm they hold.",
    },
  ]
    .map((g) => ({
      ...g,
      /* Partial ones that touch no case stay in "Affects no case here". */
      rows: flagged.filter(
        (r) => r.status === g.status && (g.status !== "partial" || n(r) > 0),
      ),
    }))
    .filter((g) => g.rows.length > 1);
  const inGroup = (r) => GROUPS.some((g) => g.rows.includes(r));
  const grouped = GROUPS.length > 0;
  const quiet = flagged.filter(
    (r) => !inGroup(r) && r.status !== "blocked" && !n(r),
  );
  const loud = flagged.filter((r) => !inGroup(r) && !quiet.includes(r));
  const waiting = flagged.filter((r) =>
    ["untested", "stale"].includes(r.status),
  );
  /* Without an in-place check: open the agent's Capabilities tab for this world. */
  const checkRow = waiting.find((r) => r.repair === "connection") || waiting[0];
  const checkButton = onCheck ? (
    <Button
      size="small"
      variant="outlined"
      disabled={checking}
      onClick={onCheck}
      sx={{ py: 0, flexShrink: 0 }}
    >
      {checking ? "Checking…" : "Run the check"}
    </Button>
  ) : null;
  const rowsShown = all ? sorted : loud;
  const quietLine = Object.entries(
    quiet.reduce((acc, r) => {
      (acc[r.status] = acc[r.status] || []).push(r.label);
      return acc;
    }, {}),
  )
    .map(
      ([st, names]) =>
        `${names.join(", ")} (${COMPAT_STATUS[st]?.label.toLowerCase() || st})`,
    )
    .join(" · ");
  const rowSx = {
    display: "grid",
    gridTemplateColumns: COLS,
    columnGap: 1.5,
    alignItems: "start",
    px: 1.5,
    py: 0.625,
    borderTop: "1px solid",
    borderColor: "divider",
  };
  const inlineOf = (r) => (onInlineFix && inlineFixOf?.(r)) || null;
  const openFix = (r) => {
    const fix = inlineOf(r);
    if (!fix) return onFix(r);
    setFixValue(fix.value || "");
    return setFixing(fixing === r.id ? null : r.id);
  };
  const fixButton = (r, label = "Fix") => (
    <Button
      size="small"
      variant="outlined"
      onClick={() => openFix(r)}
      sx={{ minWidth: 0, py: 0, flexShrink: 0 }}
    >
      {label}
    </Button>
  );
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {all
            ? [
                compatSummaryLine({ counts }),
                testedAt ? `checked ${when(testedAt)}` : "not checked yet",
              ].join(" · ")
            : testedAt
              ? `Checked ${when(testedAt)}`
              : grouped
                ? ""
                : "Not checked yet"}
        </Typography>
        {/* The check sits on the grouped row; alone it sits up here. */}
        {(all || !grouped) && waiting.length > 0 && checkButton}
        {(rows.length > loud.length || grouped) && (
          <TextLink onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show all ${rows.length}`}
          </TextLink>
        )}
      </Stack>
      {!all &&
        GROUPS.map((g, gi) => (
          <Box key={g.status} sx={rowSx}>
            <Typography sx={{ typography: "s2" }}>
              {g.title(g.rows.length)}
            </Typography>
            <CapabilityStatus status={g.status} />
            <Typography
              sx={{
                typography: "s2",
                textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {`${affectsLabel(Math.max(...g.rows.map(n)), requested)} affected`}
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ gridColumn: "1 / -1", minWidth: 0 }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {`${g.rows.map((r) => r.label).join(", ")} — ${g.why}`}
              </Typography>
              {/* One check answers every group: its button on the first. */}
              {gi === 0 &&
                (checkButton ||
                  (onFix &&
                    checkRow &&
                    fixButton(checkRow, "Check capabilities")))}
            </Stack>
          </Box>
        ))}
      {rowsShown.map((r) => (
        <Box key={r.id} sx={rowSx}>
          <Typography sx={{ typography: "s2" }}>{r.label}</Typography>
          <CapabilityStatus status={r.status} />
          <Stack
            direction="row"
            justifyContent="flex-end"
            alignItems="center"
            spacing={1}
          >
            <Typography
              sx={{
                typography: "s2",
                color: n(r) ? "text.primary" : "text.disabled",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {n(r) || r.status === "blocked" || r.status === "partial"
                ? `${affectsLabel(n(r), requested)} affected`
                : "—"}
            </Typography>
            {onFix && r.repair === "connection" && fixButton(r)}
          </Stack>
          {r.detail && (
            <Typography
              sx={{
                gridColumn: "1 / -1",
                typography: "s3",
                color: "text.tertiary",
                minWidth: 0,
              }}
            >
              {r.detail}
            </Typography>
          )}
          {fixing === r.id && inlineOf(r) && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ gridColumn: "1 / -1", mt: 0.75, mb: 0.25 }}
            >
              {inlineOf(r).kind === "field" ? (
                <>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {inlineOf(r).label}
                  </Typography>
                  <TextField
                    size="small"
                    autoFocus
                    value={fixValue}
                    onChange={(e) => setFixValue(e.target.value)}
                    sx={{
                      flex: 1,
                      "& input": {
                        fontFamily: "ui-monospace, Menlo, monospace",
                        py: 0.5,
                      },
                    }}
                  />
                </>
              ) : (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
                >
                  {`${inlineOf(r).label} — its writes land in the test world, not production.`}
                </Typography>
              )}
              <Button
                size="small"
                onClick={() => setFixing(null)}
                sx={{ color: "text.secondary" }}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="outlined"
                disabled={
                  checking || (inlineOf(r).kind === "field" && !fixValue.trim())
                }
                onClick={() => {
                  onInlineFix(r, fixValue);
                  setFixing(null);
                }}
              >
                Save and test
              </Button>
            </Stack>
          )}
        </Box>
      ))}
      {!all && quiet.length > 0 && (
        <Typography
          sx={{
            px: 1.5,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
            typography: "s3",
            color: "text.tertiary",
          }}
        >
          {`Affects no case here: ${quietLine}`}
        </Typography>
      )}
      {children && (
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
CapabilitySummary.propTypes = {
  /* { id, label, status, detail?, affected: ids | count, repair? } */
  rows: PropTypes.array.isRequired,
  requested: PropTypes.number.isRequired,
  testedAt: PropTypes.string,
  title: PropTypes.string,
  /* A row with a connection repair offers Fix. */
  onFix: PropTypes.func,
  /* Runs the connection check in place (the untested row's button). */
  onCheck: PropTypes.func,
  checking: PropTypes.bool,
  /* (row) → a repair small enough to make here ({ kind, label, value }). */
  inlineFixOf: PropTypes.func,
  /* (row, value) — saves that repair and checks again. */
  onInlineFix: PropTypes.func,
  /* Below the rows: what the run does about blocked cases. */
  children: PropTypes.node,
};
