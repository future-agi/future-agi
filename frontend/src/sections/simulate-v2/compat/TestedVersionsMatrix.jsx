import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { StatusDot } from "../components/primitives";
import { clickable, focusRing } from "../components/clickable";
import { CAPABILITIES, COMPAT_STATUS } from "../_mock/compatibility";
import {
  FAMILY_CATEGORIES,
  QUAL_CHECKS,
  QUAL_LABEL,
  TESTED_VERSIONS,
  diagnosticOf,
  qualificationMs,
  qualificationOf,
} from "../_mock/testedVersions";

const MONO = "ui-monospace, Menlo, monospace";
const COLS = "minmax(0, 1fr) 170px 14px";
const capLabel = (id) =>
  QUAL_LABEL[id] || CAPABILITIES.find((c) => c.id === id)?.label || id;
/* A family's qualification result, as its row shows it. */
const QUAL_SHOWN = {
  running: { dot: "running", text: () => "Running…" },
  passed: {
    dot: "passed",
    text: () => `Passed ${QUAL_CHECKS.length}/${QUAL_CHECKS.length}`,
  },
  limited: {
    dot: "flaky",
    text: (q) =>
      `Passed · limits: ${q.limited.map(capLabel).join(", ").toLowerCase()}`,
  },
  failed: {
    dot: "failed",
    text: (q) => `Failed: ${q.failed.map(capLabel).join(", ").toLowerCase()}`,
  },
  "not-run": { dot: "queued", text: () => "Not run yet" },
};
const STATUS_KEY = {
  Supported: "supported",
  Partial: "partial",
  Untested: "untested",
};
/* Problems first: known gaps, then untested, then supported. */
const STATUS_ORDER = { Partial: 0, Untested: 1, Supported: 2 };
const when = (iso) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

/* One family on one line; what isn't covered and its checks open below it. */
function FamilyRow({ row, mine, qual }) {
  const [open, setOpen] = useState(mine);
  const diag = diagnosticOf(row);
  const status = STATUS_KEY[row.status] || "untested";
  const limits = row.limits !== "—" ? row.limits : "";
  const checks = diag.id ? CAPABILITIES.filter((c) => diag.results[c.id]) : [];
  const misses = checks.filter((c) => diag.results[c.id] !== "supported");
  return (
    <Box
      sx={{
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
        ...(mine && { bgcolor: "action.hover" }),
      }}
    >
      <Box
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...focusRing,
          display: "grid",
          gridTemplateColumns: COLS,
          columnGap: 2,
          px: 1.5,
          py: 0.625,
          alignItems: "center",
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Typography noWrap sx={{ typography: "s2" }}>
          {row.family}
          {mine && (
            <Box
              component="span"
              sx={{ typography: "s3", color: "text.tertiary", ml: 0.75 }}
            >
              this agent
            </Box>
          )}
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
          <StatusDot
            status={
              qual ? QUAL_SHOWN[qual.outcome].dot : COMPAT_STATUS[status].dot
            }
            size={6}
            live={qual?.outcome === "running"}
          />
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {qual ? QUAL_SHOWN[qual.outcome].text(qual) : row.status}
          </Typography>
        </Stack>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={14}
          sx={{
            color: "text.tertiary",
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform .15s",
          }}
        />
      </Box>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={0.5} sx={{ px: 1.5, pb: 1 }}>
          <Typography
            sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}
          >
            {row.versions}
          </Typography>
          {limits && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`Not covered: ${limits}`}
            </Typography>
          )}
          <Stack
            direction="row"
            flexWrap="wrap"
            useFlexGap
            columnGap={1.5}
            rowGap={0.5}
            alignItems="center"
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {checks.length
                ? `${checks.length - misses.length} of ${checks.length} checks passed${diag.at ? ` · ${when(diag.at)}` : ""}`
                : "Not checked yet"}
            </Typography>
            {misses.map((c) => (
              <Stack
                key={c.id}
                direction="row"
                alignItems="center"
                spacing={0.5}
              >
                <StatusDot
                  status={COMPAT_STATUS[diag.results[c.id]].dot}
                  size={5}
                  live={false}
                />
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {c.label}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Stack>
      </Collapse>
    </Box>
  );
}
FamilyRow.propTypes = {
  row: PropTypes.object.isRequired,
  mine: PropTypes.bool,
  /* This family's qualification: { outcome, failed, limited } or running. */
  qual: PropTypes.object,
};

/**
 * The tested-versions matrix (roadmap release.md, Week 2): exact versions per
 * agent family, grouped the way the roadmap lists them, each linked to the
 * diagnostic behind its status. Untested is untested — never shown as
 * supported, and a family not listed may still connect, uncertified.
 */
export default function TestedVersionsMatrix({ highlight = null }) {
  /* Qualification: every family starts together and finishes on its own. */
  const [qual, setQual] = useState({});
  const [ranAt, setRanAt] = useState(null);
  const timers = useRef([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const running = Object.values(qual).some((q) => q.outcome === "running");
  const runQualification = () => {
    timers.current.forEach(clearTimeout);
    setRanAt(new Date());
    setQual(
      Object.fromEntries(
        TESTED_VERSIONS.map((r) => [r.family, { outcome: "running" }]),
      ),
    );
    timers.current = TESTED_VERSIONS.map((r) =>
      setTimeout(
        () => setQual((cur) => ({ ...cur, [r.family]: qualificationOf(r) })),
        qualificationMs(r),
      ),
    );
  };
  const done = Object.values(qual).filter((q) => q.outcome !== "running");
  const failedN = done.filter((q) => q.outcome === "failed").length;
  const notRunN = done.filter((q) => q.outcome === "not-run").length;
  const passedN = done.length - failedN - notRunN;
  const counts = TESTED_VERSIONS.reduce(
    (m, r) => ({ ...m, [r.status]: (m[r.status] || 0) + 1 }),
    {},
  );
  return (
    <Stack spacing={1}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {ranAt
            ? running
              ? `Qualifying ${TESTED_VERSIONS.length} families at once · ${done.length} done`
              : `Checked ${when(ranAt.toISOString())} · ${passedN} passed${failedN ? ` · ${failedN} failed` : ""}${notRunN ? ` · ${notRunN} not run` : ""}`
            : `${TESTED_VERSIONS.length} families · ${counts.Supported || 0} supported · ${counts.Partial || 0} partial · ${counts.Untested || 0} untested`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={running}
          onClick={runQualification}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {ranAt ? "Run again" : "Run checks"}
        </Button>
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflowX: "auto",
        }}
      >
        <Box>
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
            {["Family", ranAt ? "Last check" : "Status", ""].map((h) => (
              <Typography
                key={h}
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                }}
              >
                {h}
              </Typography>
            ))}
          </Box>
          {FAMILY_CATEGORIES.flatMap((cat) =>
            TESTED_VERSIONS.filter((r) => r.category === cat.id),
          )
            .sort(
              (a, b) =>
                (STATUS_ORDER[a.status] ?? 3) - (STATUS_ORDER[b.status] ?? 3),
            )
            .map((r) => (
              <FamilyRow
                key={r.family}
                row={r}
                mine={highlight === r.family}
                qual={qual[r.family]}
              />
            ))}
        </Box>
      </Box>
    </Stack>
  );
}
TestedVersionsMatrix.propTypes = {
  /* The family this agent belongs to — opened and marked. */
  highlight: PropTypes.string,
};
