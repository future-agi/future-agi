import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  TextField,
  MenuItem,
  Tooltip,
  IconButton,
  Table,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@mui/material";
import Iconify from "src/components/iconify";

/* Plain-English hint per axis — surfaces on hover of each axis row
   so a non-technical reader can decode T/W/D/X/I/O without leaving
   the panel. Kept in one place so tooltip text and future help
   copy stay in sync. */
const AXIS_HINTS = {
  T: "What the customer is trying to do (look up, cancel, execute…)",
  W: "Who the customer is (child, adult, senior, business user…)",
  D: "What mood they're in (calm, urgent, angry, confused…)",
  X: "Environment quality (clean call, noisy, dropped signal, interrupted)",
  I: "How long the conversation runs (single-turn → long session)",
  O: "Attack / trick attempts (prompt-injection, PII theft, jailbreak…)",
};
import {
  PRD_AXES,
  buildPrdMatrix,
  perAxisCoverage,
  pairwiseCoverage,
  forcedCellsCoverage,
  ruleCoverage,
} from "../../_mock/coverage";

/**
 * Coverage — PRD Story 9 (six-axis framework).
 *
 * Design goals for this pass (over the earlier iteration that read as
 * a dashboard of colored progress bars):
 *   - Numbers stand on their own; color is used sparingly and only when
 *     it means something (a hard "missing" status).
 *   - Dense tabular layout — three sections separated by dividers,
 *     matching the rest of the workspace's information density.
 *   - No prose helper lines like "these need at least one scenario each";
 *     the empty state is the fraction "0/5" and the item's own hollow dot.
 */
export default function CoverageMatrix({ scenarios, env, onClose }) {
  const [rowAxis, setRowAxis] = useState("T");
  const [colAxis, setColAxis] = useState("O");
  const m = useMemo(
    () => buildPrdMatrix(scenarios, env, rowAxis, colAxis),
    [scenarios, env, rowAxis, colAxis],
  );
  const perAxis = useMemo(
    () => perAxisCoverage(scenarios, env),
    [scenarios, env],
  );
  const pairs = useMemo(
    () => pairwiseCoverage(scenarios, env),
    [scenarios, env],
  );
  const forced = useMemo(() => forcedCellsCoverage(scenarios), [scenarios]);
  const rules = useMemo(() => ruleCoverage(env, scenarios), [env, scenarios]);
  const uncovered = rules.filter((r) => r.count === 0);

  const overall = perAxis.length
    ? perAxis.reduce((sum, a) => sum + a.ratio, 0) / perAxis.length
    : 0;
  const forcedMissing = forced.filter((f) => !f.present);
  const pairAvg = pairs.length
    ? pairs.reduce((sum, p) => sum + p.ratio, 0) / pairs.length
    : 0;

  /* Rich hover explainer — mounted as a Tooltip title (any ReactNode
     works). Hover the (i) chip to reveal, no click required. */
  const helpContent = (
    <Box sx={{ p: 0.5, maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          mb: 0.5,
          color: "common.white",
        }}
      >
        What this panel answers
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          color: (t) => alpha(t.palette.common.white, 0.85),
          mb: 1.5,
        }}
      >
        Not &ldquo;how many tests do we have&rdquo; — but{" "}
        <Box component="b" sx={{ color: "common.white" }}>
          &ldquo;what kinds of situations did we forget to test?&rdquo;
        </Box>
      </Typography>

      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: (t) => alpha(t.palette.common.white, 0.7),
          textTransform: "none",
          letterSpacing: 0.4,
          fontSize: 12,
          mb: 0.75,
        }}
      >
        Three numbers at a glance
      </Typography>
      <Typography
        component="ul"
        sx={{
          typography: "s3",
          color: (t) => alpha(t.palette.common.white, 0.85),
          pl: 2,
          mb: 1.5,
        }}
      >
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            Axes
          </Box>{" "}
          — of all the kinds of situations, what fraction are covered.
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            Pairs
          </Box>{" "}
          — of every two-way combination (e.g. task × attacker), what fraction
          are covered.
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            Forced
          </Box>{" "}
          — how many of the five dangerous must-have cases are present.
        </li>
      </Typography>

      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: (t) => alpha(t.palette.common.white, 0.7),
          textTransform: "none",
          letterSpacing: 0.4,
          fontSize: 12,
          mb: 0.75,
        }}
      >
        The six axes
      </Typography>
      <Typography
        component="ul"
        sx={{
          typography: "s3",
          color: (t) => alpha(t.palette.common.white, 0.85),
          pl: 2,
          mb: 1.5,
        }}
      >
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            T
          </Box>{" "}
          — {AXIS_HINTS.T}
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            W
          </Box>{" "}
          — {AXIS_HINTS.W}
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            D
          </Box>{" "}
          — {AXIS_HINTS.D}
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            X
          </Box>{" "}
          — {AXIS_HINTS.X}
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            I
          </Box>{" "}
          — {AXIS_HINTS.I}
        </li>
        <li>
          <Box component="b" sx={{ color: "common.white" }}>
            O
          </Box>{" "}
          — {AXIS_HINTS.O}
        </li>
      </Typography>

      <Typography
        sx={{
          typography: "s3",
          color: (t) => alpha(t.palette.common.white, 0.7),
        }}
      >
        Empty red cells in the grid below are the actual gaps — they name a
        combination nothing in the suite tests yet.
      </Typography>
    </Box>
  );
  /*
    Lives in the Coverage side drawer, so the drawer's header is this
    panel's header: one title, the explainer, the three numbers and close.
    Nothing folds — opening the drawer is already the choice to see it.
  */
  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2.5}
        sx={{
          px: 2.5,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              Coverage
            </Typography>
            <Tooltip
              arrow
              title={helpContent}
              placement="right-start"
              enterDelay={80}
              leaveDelay={100}
              slotProps={{
                tooltip: {
                  sx: {
                    maxWidth: 420,
                  },
                  onClick: (e) => e.stopPropagation(),
                },
              }}
            >
              <Box
                component="span"
                onClick={(e) => e.stopPropagation()}
                sx={{
                  display: "inline-flex",
                  cursor: "help",
                  color: "text.tertiary",
                  "&:hover": { color: "text.primary" },
                }}
                aria-label="What is coverage?"
              >
                <Iconify icon="solar:info-circle-linear" width={16} />
              </Box>
            </Tooltip>
          </Stack>
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            {`${scenarios.length} scenarios · what kinds of situations did we forget to test?`}
          </Typography>
        </Box>
        <SummaryStat
          label="Axes"
          value={`${Math.round(overall * 100)}%`}
          color={toneText(overall)}
        />
        <SummaryStat
          label="Pairs"
          value={`${Math.round(pairAvg * 100)}%`}
          color={toneText(pairAvg)}
        />
        <SummaryStat
          label="Forced"
          value={`${forced.length - forcedMissing.length}/${forced.length}`}
          color={toneText(
            (forced.length - forcedMissing.length) / Math.max(1, forced.length),
          )}
        />
        {onClose && (
          <IconButton
            size="small"
            onClick={onClose}
            aria-label="Close coverage"
          >
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        )}
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        {/* ── AXES TABLE ─────────────────────────────────────────────────── */}
        <Table
          size="small"
          sx={{
            "& .MuiTableCell-root": {
              borderBottom: "1px solid",
              borderColor: "divider",
              py: 1,
            },
          }}
        >
          <TableHead>
            <TableRow
              sx={{
                "&:last-of-type .MuiTableCell-root": { borderColor: "divider" },
              }}
            >
              <TableCell sx={headerCellSx} width={220}>
                Axis
              </TableCell>
              <TableCell sx={headerCellSx} width={110} align="right">
                Covered
              </TableCell>
              <TableCell sx={headerCellSx}>Missing levels</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {perAxis.map((a) => (
              <TableRow key={a.id} hover>
                <TableCell>
                  <Tooltip
                    arrow
                    placement="right"
                    title={AXIS_HINTS[a.id] || ""}
                  >
                    <Stack
                      direction="row"
                      alignItems="baseline"
                      spacing={1}
                      sx={{ cursor: "help", width: "fit-content" }}
                    >
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: 600,
                          color: "text.primary",
                          width: 12,
                        }}
                      >
                        {a.id}
                      </Typography>
                      <Typography
                        sx={{ typography: "s2", color: "text.primary" }}
                      >
                        {a.label}
                      </Typography>
                    </Stack>
                  </Tooltip>
                </TableCell>
                <TableCell align="right">
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    justifyContent="flex-end"
                  >
                    <MicroBar ratio={a.ratio} />
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: 600,
                        fontVariantNumeric: "tabular-nums",
                        color: toneText(a.ratio),
                        minWidth: 36,
                        textAlign: "right",
                      }}
                    >
                      {a.hit}/{a.total}
                    </Typography>
                  </Stack>
                </TableCell>
                <TableCell>
                  {a.missingLevels.length === 0 ? (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      All levels covered
                    </Typography>
                  ) : (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                      noWrap
                      title={a.missingLevels.map((m) => m.label).join(" · ")}
                    >
                      {a.missingLevels.map((m) => m.label).join(" · ")}
                    </Typography>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {/* ── FORCED OVERLAYS ─────────────────────────────────────────────── */}
        <Box
          sx={{
            px: 2.5,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={1}
            sx={{ mb: 1 }}
          >
            <Typography
              sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
            >
              Forced overlays
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Always included, whatever the sample
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color:
                  forcedMissing.length > 0
                    ? "status.danger.fg"
                    : "text.primary",
              }}
            >
              {forced.length - forcedMissing.length}/{forced.length}
            </Typography>
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "1fr 1fr",
                md: "1fr 1fr 1fr",
              },
              rowGap: 0.75,
              columnGap: 3,
            }}
          >
            {forced.map((f) => (
              <Stack key={f.id} direction="row" alignItems="center" spacing={1}>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: f.present ? "status.success.solid" : "transparent",
                    border: "1px solid",
                    borderColor: f.present
                      ? "status.success.solid"
                      : (t) => alpha(t.palette.text.primary, 0.4),
                    flexShrink: 0,
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    color: f.present ? "text.primary" : "text.tertiary",
                  }}
                >
                  {f.label}
                </Typography>
              </Stack>
            ))}
          </Box>
        </Box>

        {/* ── PAIRWISE HEATMAP ────────────────────────────────────────────── */}
        <Box sx={{ px: 2.5, py: 1.5 }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ mb: 1.5 }}
          >
            <Typography
              sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
            >
              Pairwise · {m.rowAxis.label} × {m.colAxis.label}
            </Typography>
            <Box sx={{ flex: 1 }} />
            <AxisPick
              label="Rows"
              value={rowAxis}
              onChange={setRowAxis}
              exclude={colAxis}
            />
            <AxisPick
              label="Columns"
              value={colAxis}
              onChange={setColAxis}
              exclude={rowAxis}
            />
          </Stack>
          <Box sx={{ overflowX: "auto" }}>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: `minmax(140px, max-content) repeat(${m.colKeys.length}, minmax(72px, 1fr))`,
                gap: 0.5,
                minWidth: "max-content",
              }}
            >
              <Box />
              {m.colKeys.map((c) => (
                <Typography
                  key={c}
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    textAlign: "center",
                    pb: 0.5,
                    textTransform: "none",
                    letterSpacing: 0.3,
                    fontSize: 12,
                  }}
                >
                  {m.colAxis.labelOf(c)}
                </Typography>
              ))}

              {m.rowKeys.map((r) => (
                <Box key={r} sx={{ display: "contents" }}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      alignSelf: "center",
                      pr: 1.5,
                      fontSize: 13,
                    }}
                  >
                    {m.rowAxis.labelOf(r)}
                  </Typography>
                  {m.colKeys.map((c) => {
                    const n = m.at(r, c);
                    /* Green ramp for covered, red dashed for empty — the
                     idiom the earlier iteration used, which lets each
                     cell read as its own chip rather than mashing into
                     a wall of red. */
                    const t = m.max > 1 ? (n - 1) / (m.max - 1) : n > 0 ? 1 : 0;
                    const opacity = n ? 0.08 + 0.22 * t : 0;
                    const strong = n && n / m.max >= 0.85;
                    return (
                      <Tooltip
                        key={c}
                        arrow
                        title={
                          n
                            ? `${n} scenario${n === 1 ? "" : "s"} — ${m.rowAxis.labelOf(r)} × ${m.colAxis.labelOf(c)}`
                            : `Empty — ${m.rowAxis.labelOf(r)} × ${m.colAxis.labelOf(c)}`
                        }
                      >
                        <Box
                          sx={{
                            height: 40,
                            borderRadius: 1,
                            display: "grid",
                            placeItems: "center",
                            border: "1px solid",
                            borderColor: n
                              ? "status.success.border"
                              : "status.danger.border",
                            borderStyle: n ? "solid" : "dashed",
                            bgcolor: n
                              ? (t) =>
                                  alpha(t.palette.status.success.solid, opacity)
                              : "status.danger.bg",
                          }}
                        >
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              fontVariantNumeric: "tabular-nums",
                              color: n
                                ? strong
                                  ? "common.white"
                                  : "text.primary"
                                : "status.danger.fg",
                            }}
                          >
                            {n || "—"}
                          </Typography>
                        </Box>
                      </Tooltip>
                    );
                  })}
                </Box>
              ))}
            </Box>
          </Box>
        </Box>

        {/* ── GUARDRAILS ──────────────────────────────────────────────────── */}
        <Box
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={1}
            sx={{ mb: 1 }}
          >
            <Typography
              sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
            >
              Guardrail coverage
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Every declared rule and whether any scenario tests it
            </Typography>
            <Box sx={{ flex: 1 }} />
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color:
                  uncovered.length > 0 ? "status.danger.fg" : "text.primary",
              }}
            >
              {rules.length - uncovered.length}/{rules.length}
            </Typography>
          </Stack>
          <Stack spacing={0.5}>
            {rules.map((r) => (
              <Stack
                key={r.rule}
                direction="row"
                alignItems="flex-start"
                spacing={1.25}
              >
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: r.count ? "status.success.solid" : "transparent",
                    border: "1px solid",
                    borderColor: r.count
                      ? "status.success.solid"
                      : (t) => alpha(t.palette.text.primary, 0.4),
                    flexShrink: 0,
                    mt: "5px",
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    color: r.count ? "text.primary" : "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {r.rule}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: r.count ? "text.tertiary" : "status.danger.fg",
                    flexShrink: 0,
                  }}
                >
                  {r.count
                    ? `${r.count} scenario${r.count === 1 ? "" : "s"}`
                    : "no coverage"}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Box>
    </Stack>
  );
}

CoverageMatrix.propTypes = {
  env: PropTypes.object,
  scenarios: PropTypes.array.isRequired,
  onClose: PropTypes.func,
};

/* The product's DataTable header: no fill (the theme's grey gradient off), a quiet 13px title. */
const headerCellSx = {
  fontSize: 13,
  fontWeight: 500,
  color: "text.secondary",
  py: 1,
  bgcolor: "background.paper",
  backgroundImage: "none",
};

function SummaryStat({ label, value, color }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.75}>
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", fontSize: 12 }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          color: color || "text.primary",
        }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
SummaryStat.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  color: PropTypes.string,
};

/** Compact fill bar — 44×3px density cue next to the fraction. Fill
 *  is toned from red → amber → green depending on ratio. */
function MicroBar({ ratio }) {
  const pct = Math.max(0, Math.min(1, ratio || 0));
  return (
    <Box
      sx={{
        width: 44,
        height: 3,
        borderRadius: 999,
        bgcolor: "action.selected",
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <Box
        sx={{
          width: `${pct * 100}%`,
          height: "100%",
          bgcolor: toneColor(ratio),
          transition: "width 0.15s ease",
        }}
      />
    </Box>
  );
}
MicroBar.propTypes = { ratio: PropTypes.number };

/** Traffic-light tone for a 0..1 ratio.
 *   0             → red
 *   0 < r <= 0.34 → red (still concerning)
 *   0.34 < r < 0.67 → amber
 *   >= 0.67       → green
 *  Kept as a single function so every place (fractions, micro-bars,
 *  header stats) tiers on the same thresholds. */
function toneColor(ratio) {
  const r = Number(ratio) || 0;
  if (r < 0.34) return "status.danger.solid";
  if (r < 0.67) return "status.warning.solid";
  return "status.success.solid";
}

/** Same tiers for text — the amber middle band reads as neutral words; the
 *  micro-bar beside it carries the colour. */
function toneText(ratio) {
  const r = Number(ratio) || 0;
  if (r < 0.34) return "status.danger.fg";
  if (r < 0.67) return "text.primary";
  return "status.success.fg";
}

function AxisPick({ label, value, onChange, exclude }) {
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      sx={{
        minWidth: 110,
        "& .MuiInputBase-input": { typography: "s2", py: 0.5, fontSize: 12 },
      }}
    >
      {PRD_AXES.filter((a) => a.id !== exclude).map((a) => (
        <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>
          {a.id} · {a.label}
        </MenuItem>
      ))}
    </TextField>
  );
}
AxisPick.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
  exclude: PropTypes.string,
};
