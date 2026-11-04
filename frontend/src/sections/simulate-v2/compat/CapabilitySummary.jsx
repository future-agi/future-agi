import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
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
  children,
}) {
  const [all, setAll] = useState(false);
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
  const shown = all ? sorted : flagged;
  const n = (r) =>
    Array.isArray(r.affected) ? r.affected.length : r.affected || 0;
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
          {[
            compatSummaryLine({ counts }),
            testedAt ? `checked ${when(testedAt)}` : "not checked yet",
          ].join(" · ")}
        </Typography>
        {rows.length > flagged.length && (
          <TextLink onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `Show all ${rows.length}`}
          </TextLink>
        )}
      </Stack>
      {shown.map((r) => (
        <Box
          key={r.id}
          sx={{
            display: "grid",
            gridTemplateColumns: COLS,
            columnGap: 1.5,
            alignItems: "start",
            px: 1.5,
            py: 0.625,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
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
            {onFix && r.repair === "connection" && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onFix(r)}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  minWidth: 0,
                  py: 0,
                }}
              >
                Fix
              </Button>
            )}
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
        </Box>
      ))}
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
  /* Below the rows: what the run does about blocked cases. */
  children: PropTypes.node,
};
