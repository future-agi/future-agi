// Paths · `Top paths` (R4 lane T, D14): each path as step chips (`×2`
// badges), share bar, tries, fail rate (red tint), example tasks
// (`T1 T2 T11 +3`). Compare mode: `Share #2B · Share #3A` and the
// `New in #3A` / `Gone in #3A` chips; Δ, fail per run and examples are one
// click away under `Columns ▾`. Keyboard: the host moves the cursor (J/K)
// and opens the cursor row's examples (Enter). iterate r1 G3 (P1): every
// failing path gets `Fix this path` (Test a fix on the run it fails in).
import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  alpha,
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtPct, fmtPts, NUM_SX } from "src/sections/common/format/fmt";
import { failingText } from "./stepCompare";

export const PATH_COLUMNS = Object.freeze([
  { id: "share", label: "Share", always: true },
  { id: "tries", label: "Tries", always: true },
  { id: "fail", label: "Fail rate", always: true },
  { id: "delta", label: "Δ share", compareOnly: true },
  { id: "failEach", label: "Fail rate of the compared run", compareOnly: true },
  { id: "examples", label: "Example tasks" },
]);

const pct0 = (v) => fmtPct(v, { dp: 0 });

function StepChips({ steps, dense }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      gap={0.25}
      sx={{ minWidth: 0 }}
    >
      {steps.map((s, i) => (
        <React.Fragment key={`${s.name}-${i}`}>
          {i ? (
            <Iconify
              icon="mdi:chevron-right"
              width={12}
              sx={{ color: "text.disabled" }}
            />
          ) : null}
          <Box
            component="span"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              px: 0.625,
              py: dense ? 0 : "1px",
              borderRadius: 0.75,
              border: "1px solid",
              borderColor: "divider",
              typography: "s3",
              fontFamily: "monospace",
              whiteSpace: "nowrap",
            }}
          >
            {s.name}
            {s.repeat > 1 ? (
              <Box
                component="span"
                sx={{
                  ml: 0.25,
                  px: 0.375,
                  borderRadius: 0.5,
                  bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
                  color: "warning.dark",
                  fontWeight: 600,
                  fontFamily: "inherit",
                }}
              >
                {`×${s.repeat}`}
              </Box>
            ) : null}
            {/* W1-9 B4: tries on this path whose check failed on the step. */}
            {failingText(s) ? (
              <Box
                component="span"
                data-testid="path-step-failing"
                sx={{ ml: 0.25, color: "accent.fail", fontWeight: 600 }}
              >
                {failingText(s)}
              </Box>
            ) : null}
          </Box>
        </React.Fragment>
      ))}
    </Stack>
  );
}
StepChips.propTypes = { steps: PropTypes.array, dense: PropTypes.bool };

function ShareCell({ share, color }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ ...NUM_SX }}>
      <Box
        sx={{
          width: 44,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.hover",
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            width: `${Math.round((share ?? 0) * 100)}%`,
            height: "100%",
            bgcolor: color ?? "primary.main",
          }}
        />
      </Box>
      <Typography typography="s2" sx={{ minWidth: 34, textAlign: "right" }}>
        {pct0(share ?? 0)}
      </Typography>
    </Stack>
  );
}
ShareCell.propTypes = { share: PropTypes.number, color: PropTypes.string };

function FailCell({ v }) {
  if (v == null)
    return (
      <Typography typography="s2" color="text.disabled" sx={NUM_SX}>
        —
      </Typography>
    );
  return (
    <Box
      component="span"
      sx={{
        ...NUM_SX,
        typography: "s2",
        px: 0.75,
        py: "1px",
        borderRadius: 0.75,
        bgcolor: (t) =>
          v > 0 ? alpha(t.palette.error.main, 0.06 + 0.16 * v) : "transparent",
        color: v >= 0.5 ? "error.main" : "text.primary",
      }}
    >
      {pct0(v)}
    </Box>
  );
}
FailCell.propTypes = { v: PropTypes.number };

const head = (label) => (
  <Typography
    typography="s3"
    color="text.secondary"
    fontWeight="fontWeightMedium"
    noWrap
  >
    {label}
  </Typography>
);
const num = (v) => (
  <Typography typography="s2" sx={NUM_SX}>
    {v ?? "—"}
  </Typography>
);

/**
 * Columns in order. Compare mode: `a` = this run, `b` = Compare with; the
 * default set is Share a · Share b · Tries a · Fail a; Δ, `Fail b` and the
 * example tasks are under `Columns ▾`.
 */
export function pathColumns({ compare, labels, colors, optional = [] }) {
  const on = (id) => optional.includes(id);
  const ex = (r) => (compare ? r.a ?? r.b : r);
  const cols = compare
    ? [
        {
          id: "shareA",
          w: "92px",
          head: `Share ${labels.a}`,
          cell: (r) => <ShareCell share={r.shareA} color={colors.a} />,
        },
        {
          id: "shareB",
          w: "92px",
          head: `Share ${labels.b}`,
          cell: (r) => <ShareCell share={r.shareB} color={colors.b} />,
        },
        {
          id: "tries",
          w: "72px",
          head: `Tries ${labels.a}`,
          cell: (r) => num(r.a?.attempts),
        },
        {
          id: "fail",
          w: "72px",
          head: `Fail ${labels.a}`,
          cell: (r) => <FailCell v={r.failA} />,
        },
        on("delta") && {
          id: "delta",
          w: "76px",
          head: "Δ share",
          cell: (r) => num(fmtPts(r.delta)),
        },
        on("failEach") && {
          id: "failB",
          w: "76px",
          head: `Fail ${labels.b}`,
          cell: (r) => <FailCell v={r.failB} />,
        },
      ]
    : [
        {
          id: "share",
          w: "92px",
          head: "Share",
          cell: (r) => <ShareCell share={r.share} color={colors.a} />,
        },
        { id: "tries", w: "56px", head: "Tries", cell: (r) => num(r.attempts) },
        {
          id: "fail",
          w: "64px",
          head: "Fail",
          cell: (r) => <FailCell v={r.fail_share} />,
        },
      ];
  if (on("examples"))
    cols.push({
      id: "examples",
      w: "112px",
      head: "Example tasks",
      left: true,
      cell: (r) => {
        const p = ex(r);
        const labelsOf = (p?.example_labels ?? []).filter(Boolean);
        const more = (p?.example_count ?? 0) - Math.min(3, labelsOf.length);
        return (
          <Typography typography="s2" color="text.secondary" noWrap sx={NUM_SX}>
            {[labelsOf.slice(0, 3).join(" "), more > 0 ? `+${more}` : null]
              .filter(Boolean)
              .join(" ") || "—"}
          </Typography>
        );
      },
    });
  return cols.filter(Boolean);
}

/**
 * @param {{rows: object[], compare: boolean, labels: {a, b}, colors: {a, b},
 *   columns: string[], cursor: number, onCursor: (i) => void,
 *   onOpen: (row) => void}} props  `rows` = comparePaths rows in compare
 *   mode, else aggregate paths; `columns` = the optional columns shown
 */
export default function PathsTable({
  rows,
  compare,
  labels,
  colors,
  columns,
  cursor,
  onCursor,
  onOpen,
  fixFor,
  onFix,
  fixDisabledReason,
}) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current
      ?.querySelector(`[data-row-index="${cursor}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor]);
  const cols = pathColumns({ compare, labels, colors, optional: columns });
  const template = ["minmax(200px, 1fr)", ...cols.map((c) => c.w), "32px"].join(
    " ",
  );
  const cellSx = {
    px: 1,
    py: 0.75,
    minWidth: 0,
    display: "flex",
    alignItems: "center",
  };
  const align = (c) => (c.left ? "flex-start" : "flex-end");
  return (
    <Box
      ref={ref}
      role="grid"
      aria-label="Top paths"
      sx={{ overflow: "auto", minHeight: 0 }}
      data-testid="paths-table"
    >
      <Box
        role="row"
        sx={{
          display: "grid",
          gridTemplateColumns: template,
          position: "sticky",
          top: 0,
          zIndex: 1,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
          "& > *": cellSx,
        }}
      >
        <Box>{head("Path")}</Box>
        {cols.map((c) => (
          <Box key={c.id} sx={{ justifyContent: align(c) }}>
            {head(c.head)}
          </Box>
        ))}
        <Box />
      </Box>
      {rows.map((r, i) => {
        const p = compare ? r.a ?? r.b : r;
        const n = p?.example_rows?.length ?? 0;
        const on = i === cursor;
        const fix = fixFor?.(r) ?? null;
        return (
          <Box
            key={r.id}
            role="row"
            aria-selected={on}
            data-row-index={i}
            onClick={() => onCursor(i)}
            onDoubleClick={() => onOpen(r)}
            sx={{
              display: "grid",
              gridTemplateColumns: template,
              cursor: "pointer",
              borderBottom: "1px solid",
              borderColor: "divider",
              bgcolor: on ? "action.selected" : "transparent",
              boxShadow: on
                ? (t) => `inset 3px 0 0 ${t.palette.primary.main}`
                : "none",
              "&:hover": { bgcolor: on ? "action.selected" : "action.hover" },
              "& > *": cellSx,
            }}
          >
            <Stack
              gap={0.5}
              sx={{
                flexDirection: "column",
                alignItems: "flex-start !important",
              }}
            >
              <StepChips steps={p.steps} />
              {r.chip || fix ? (
                <Stack direction="row" alignItems="center" gap={0.75}>
                  {r.chip ? (
                    <Chip
                      size="small"
                      label={`${r.chip === "new" ? "New in" : "Gone in"} ${labels.b}`}
                      sx={{
                        height: 18,
                        typography: "s3",
                        color: "text.primary",
                        bgcolor: (t) =>
                          alpha(
                            r.chip === "new"
                              ? colors.b ?? t.palette.info.main
                              : t.palette.text.secondary,
                            0.14,
                          ),
                      }}
                    />
                  ) : null}
                  {fix ? (
                    <CustomTooltip
                      show
                      size="small"
                      type="black"
                      title={
                        fixDisabledReason ??
                        `Open Test a fix on ${fix.label} at the step this path goes wrong`
                      }
                    >
                      <span>
                        <Button
                          size="small"
                          variant="outlined"
                          color="primary"
                          disabled={Boolean(fixDisabledReason)}
                          data-testid="fix-this-path"
                          startIcon={
                            <Iconify icon="mdi:source-branch" width={14} />
                          }
                          onClick={(e) => {
                            e.stopPropagation();
                            onFix?.(r);
                          }}
                          sx={{
                            height: 24,
                            px: 1,
                            typography: "s3",
                            fontWeight: 600,
                            whiteSpace: "nowrap",
                          }}
                        >
                          Fix this path
                        </Button>
                      </span>
                    </CustomTooltip>
                  ) : null}
                </Stack>
              ) : null}
            </Stack>
            {cols.map((c) => (
              <Box key={c.id} sx={{ justifyContent: align(c) }}>
                {c.cell(r)}
              </Box>
            ))}
            <Box sx={{ justifyContent: "flex-end", px: "0 !important" }}>
              <CustomTooltip
                show
                size="small"
                type="black"
                title={
                  n
                    ? `Open ${Math.min(3, n)} examples side by side (↵)`
                    : "No example tasks"
                }
              >
                <span>
                  <IconButton
                    size="small"
                    disabled={!n}
                    aria-label="Open examples side by side"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(r);
                    }}
                  >
                    <Iconify icon="mdi:compare-horizontal" width={16} />
                  </IconButton>
                </span>
              </CustomTooltip>
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}

PathsTable.propTypes = {
  rows: PropTypes.array.isRequired,
  compare: PropTypes.bool,
  labels: PropTypes.object,
  colors: PropTypes.object,
  columns: PropTypes.arrayOf(PropTypes.string),
  cursor: PropTypes.number,
  onCursor: PropTypes.func,
  onOpen: PropTypes.func,
  /** iterate r1 G3 (P1): `(row) => {label} | null` (the run it fixes). */
  fixFor: PropTypes.func,
  onFix: PropTypes.func,
  fixDisabledReason: PropTypes.string,
};
