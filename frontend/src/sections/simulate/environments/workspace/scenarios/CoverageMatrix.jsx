import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, TextField, MenuItem, Tooltip, Collapse, IconButton,
  Table, TableBody, TableHead, TableRow, TableCell,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import {
  PRD_AXES,
  buildPrdMatrix,
  perAxisCoverage,
  pairwiseCoverage,
  forcedCellsCoverage,
  ruleCoverage,
} from "src/api/simulate-environments/_fixtures/coverage";

const RED = BUILD_TONES.red;
const AMBER = BUILD_TONES.amber;
const GREEN = BUILD_TONES.green;

// Plain-English hint per axis — surfaces on hover of each axis row so a
// non-technical reader can decode T/W/D/X/I/O without leaving the panel.
const AXIS_HINTS = {
  T: "What the customer is trying to do (look up, cancel, execute…)",
  W: "Who the customer is (child, adult, senior, business user…)",
  D: "What mood they're in (calm, urgent, angry, confused…)",
  X: "Environment quality (clean call, noisy, dropped signal, interrupted)",
  I: "How long the conversation runs (single-turn → long session)",
  O: "Attack / trick attempts (prompt-injection, PII theft, jailbreak…)",
};

// Coverage — the PRD six-axis framework.
//
// A scenario count is not coverage. Every scenario is a coordinate over
// T · W · D · X · I · O; this panel reports what fraction of each axis, and of
// the two-way combinations, the suite actually covers — the empty cells are the
// finding. Collapsed by default when it sits above the list: the header carries
// the three summary numbers (Axes / Pairs / Forced), the chevron unfurls the
// full per-axis table, forced-overlays checklist, pairwise heatmap and
// guardrail list.
export default function CoverageMatrix({ scenarios, env, defaultExpanded = false }) {
  const [rowAxis, setRowAxis] = useState("T");
  const [colAxis, setColAxis] = useState("O");
  const [expanded, setExpanded] = useState(defaultExpanded);
  const m = useMemo(() => buildPrdMatrix(scenarios, env, rowAxis, colAxis), [scenarios, env, rowAxis, colAxis]);
  const perAxis = useMemo(() => perAxisCoverage(scenarios, env), [scenarios, env]);
  const pairs = useMemo(() => pairwiseCoverage(scenarios, env), [scenarios, env]);
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

  const toggle = () => setExpanded((v) => !v);

  // Rich hover explainer — mounted as a Tooltip title (any ReactNode works).
  const helpContent = (
    <Box sx={{ p: 0.5, maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
      <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.5, color: "common.white" }}>
        What this panel answers
      </Typography>
      <Typography sx={{ typography: "s2", color: (t) => alpha(t.palette.common.white, 0.85), mb: 1.5 }}>
        Not &quot;how many tests do we have&quot; — but{" "}
        <Box component="b" sx={{ color: "common.white" }}>
          &quot;what kinds of situations did we forget to test?&quot;
        </Box>
      </Typography>

      <Typography sx={{ typography: "s3", fontWeight: 700, color: (t) => alpha(t.palette.common.white, 0.7), textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 0.75 }}>
        Three numbers at a glance
      </Typography>
      <Typography component="ul" sx={{ typography: "s3", color: (t) => alpha(t.palette.common.white, 0.85), pl: 2, mb: 1.5 }}>
        <li><Box component="b" sx={{ color: "common.white" }}>Axes</Box> — of all the kinds of situations, what fraction are covered.</li>
        <li><Box component="b" sx={{ color: "common.white" }}>Pairs</Box> — of every two-way combination (e.g. task × attacker), what fraction are covered.</li>
        <li><Box component="b" sx={{ color: "common.white" }}>Forced</Box> — how many of the five dangerous must-have cases are present.</li>
      </Typography>

      <Typography sx={{ typography: "s3", fontWeight: 700, color: (t) => alpha(t.palette.common.white, 0.7), textTransform: "uppercase", letterSpacing: 0.4, fontSize: 10.5, mb: 0.75 }}>
        The six axes
      </Typography>
      <Typography component="ul" sx={{ typography: "s3", color: (t) => alpha(t.palette.common.white, 0.85), pl: 2, mb: 1.5 }}>
        <li><Box component="b" sx={{ color: "common.white" }}>T</Box> — {AXIS_HINTS.T}</li>
        <li><Box component="b" sx={{ color: "common.white" }}>W</Box> — {AXIS_HINTS.W}</li>
        <li><Box component="b" sx={{ color: "common.white" }}>D</Box> — {AXIS_HINTS.D}</li>
        <li><Box component="b" sx={{ color: "common.white" }}>X</Box> — {AXIS_HINTS.X}</li>
        <li><Box component="b" sx={{ color: "common.white" }}>I</Box> — {AXIS_HINTS.I}</li>
        <li><Box component="b" sx={{ color: "common.white" }}>O</Box> — {AXIS_HINTS.O}</li>
      </Typography>

      <Typography sx={{ typography: "s3", color: (t) => alpha(t.palette.common.white, 0.7) }}>
        Empty red cells in the grid below are the actual gaps — they name a combination nothing in the suite tests yet.
      </Typography>
    </Box>
  );

  return (
    // The wrapper carries click-to-expand — SectionCard's own header has no
    // onClick, so we own that one level up. The nested Collapse animates the body.
    <Box onClick={toggle} sx={{ cursor: "pointer" }}>
      <SectionCard
        title={
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <span>Coverage</span>
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
                    p: 1.5,
                    bgcolor: "grey.900",
                    color: "common.white",
                    boxShadow: (t) => `0 12px 40px ${alpha(t.palette.common.black, 0.4)}`,
                  },
                  onClick: (e) => e.stopPropagation(),
                },
                arrow: { sx: { color: "grey.900" } },
              }}
            >
              <Box
                component="span"
                onClick={(e) => e.stopPropagation()}
                sx={{
                  display: "inline-flex", cursor: "help",
                  color: "text.subtitle",
                  "&:hover": { color: "text.primary" },
                }}
                aria-label="What is coverage?"
              >
                <Iconify icon="solar:info-circle-linear" width={15} />
              </Box>
            </Tooltip>
          </Stack>
        }
        subtitle={expanded
          ? `${scenarios.length} scenarios · what kinds of situations did we forget to test?`
          : `${scenarios.length} scenarios · click to expand — what did we forget to test?`}
        action={
          <Stack direction="row" spacing={2.5} alignItems="center">
            <SummaryStat label="Axes" value={`${Math.round(overall * 100)}%`} color={toneColor(overall)} />
            <SummaryStat label="Pairs" value={`${Math.round(pairAvg * 100)}%`} color={toneColor(pairAvg)} />
            <SummaryStat label="Forced" value={`${forced.length - forcedMissing.length}/${forced.length}`} color={toneColor((forced.length - forcedMissing.length) / Math.max(1, forced.length))} />
            <IconButton
              size="small"
              onClick={(e) => { e.stopPropagation(); toggle(); }}
              sx={{ color: "text.subtitle" }}
              aria-label={expanded ? "Collapse coverage" : "Expand coverage"}
            >
              <Iconify
                icon="solar:alt-arrow-down-linear" width={16}
                sx={{ transition: "transform 0.15s ease", transform: expanded ? "rotate(180deg)" : "none" }}
              />
            </IconButton>
          </Stack>
        }
      >
        {/* Body doesn't inherit click-to-expand — clicking inside (axis picker,
            tooltips) shouldn't fold it away. Stop propagation at the wrapper. */}
        <Collapse in={expanded} timeout="auto" unmountOnExit onClick={(e) => e.stopPropagation()}>
          {/* Axes table */}
          <Table size="small" sx={{
            "& .MuiTableCell-root": {
              borderBottom: "1px solid",
              borderColor: "divider",
              py: 1,
            },
          }}>
            <TableHead>
              <TableRow>
                <TableCell sx={headerCellSx} width={220}>Axis</TableCell>
                <TableCell sx={headerCellSx} width={110} align="right">Covered</TableCell>
                <TableCell sx={headerCellSx}>Missing levels</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {perAxis.map((a) => (
                <TableRow key={a.id} hover>
                  <TableCell>
                    <Tooltip arrow placement="right" title={AXIS_HINTS[a.id] || ""}>
                      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ cursor: "help", width: "fit-content" }}>
                        <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", width: 12 }}>
                          {a.id}
                        </Typography>
                        <Typography sx={{ typography: "s2", color: "text.primary" }}>
                          {a.label}
                        </Typography>
                      </Stack>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" alignItems="center" spacing={1} justifyContent="flex-end">
                      <MicroBar ratio={a.ratio} />
                      <Typography sx={{
                        typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums",
                        color: toneColor(a.ratio),
                        minWidth: 36, textAlign: "right",
                      }}>
                        {a.hit}/{a.total}
                      </Typography>
                    </Stack>
                  </TableCell>
                  <TableCell>
                    {a.missingLevels.length === 0 ? (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                        All levels covered
                      </Typography>
                    ) : (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }} noWrap title={a.missingLevels.map((lvl) => lvl.label).join(" · ")}>
                        {a.missingLevels.map((lvl) => lvl.label).join(" · ")}
                      </Typography>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {/* Forced overlays */}
          <Box sx={{ px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
            <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
                Forced overlays
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Must be present regardless of sampling
              </Typography>
              <Box sx={{ flex: 1 }} />
              <Typography sx={{
                typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums",
                color: forcedMissing.length > 0 ? RED : "text.primary",
              }}>
                {forced.length - forcedMissing.length}/{forced.length}
              </Typography>
            </Stack>
            <Box sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
              rowGap: 0.75, columnGap: 3,
            }}>
              {forced.map((f) => (
                <Stack key={f.id} direction="row" alignItems="center" spacing={1}>
                  <Box sx={{
                    width: 8, height: 8, borderRadius: "50%",
                    bgcolor: f.present ? GREEN : "transparent",
                    border: "1px solid",
                    borderColor: f.present ? GREEN : (t) => alpha(t.palette.text.primary, 0.4),
                    flexShrink: 0,
                  }} />
                  <Typography sx={{
                    typography: "s2",
                    color: f.present ? "text.primary" : "text.subtitle",
                  }}>
                    {f.label}
                  </Typography>
                </Stack>
              ))}
            </Box>
          </Box>

          {/* Pairwise heatmap */}
          <Box sx={{ px: 2.5, py: 1.5 }}>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
                Pairwise · {m.rowAxis.label} × {m.colAxis.label}
              </Typography>
              <Box sx={{ flex: 1 }} />
              <AxisPick label="Rows" value={rowAxis} onChange={setRowAxis} exclude={colAxis} />
              <AxisPick label="Columns" value={colAxis} onChange={setColAxis} exclude={rowAxis} />
            </Stack>
            <Box sx={{ overflowX: "auto" }}>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: `minmax(140px, max-content) repeat(${m.colKeys.length}, minmax(72px, 1fr))`,
                  gap: 0.5, minWidth: "max-content",
                }}
              >
                <Box />
                {m.colKeys.map((c) => (
                  <Typography
                    key={c}
                    sx={{
                      typography: "s3", fontWeight: 700, color: "text.subtitle",
                      textAlign: "center", pb: 0.5, textTransform: "uppercase",
                      letterSpacing: 0.3, fontSize: 10.5,
                    }}
                  >
                    {m.colAxis.labelOf(c)}
                  </Typography>
                ))}

                {m.rowKeys.map((r) => (
                  <Box key={r} sx={{ display: "contents" }}>
                    <Typography sx={{
                      typography: "s2", fontWeight: 600, alignSelf: "center",
                      pr: 1.5, fontSize: 12.5,
                    }}>
                      {m.rowAxis.labelOf(r)}
                    </Typography>
                    {m.colKeys.map((c) => {
                      const n = m.at(r, c);
                      // Green ramp for covered, red dashed for empty — each cell
                      // reads as its own chip rather than a wall of red.
                      const t = m.max > 1 ? (n - 1) / (m.max - 1) : (n > 0 ? 1 : 0);
                      const opacity = n ? 0.08 + 0.22 * t : 0;
                      const strong = n && n / m.max >= 0.85;
                      return (
                        <Tooltip
                          key={c} arrow
                          title={n
                            ? `${n} scenario${n === 1 ? "" : "s"} — ${m.rowAxis.labelOf(r)} × ${m.colAxis.labelOf(c)}`
                            : `Empty — ${m.rowAxis.labelOf(r)} × ${m.colAxis.labelOf(c)}`}
                        >
                          <Box
                            sx={{
                              height: 40, borderRadius: 1, display: "grid", placeItems: "center",
                              border: "1px solid",
                              borderColor: n
                                ? alpha(GREEN, 0.12 + 0.2 * t)
                                : alpha(RED, 0.28),
                              borderStyle: n ? "solid" : "dashed",
                              bgcolor: n
                                ? alpha(GREEN, opacity)
                                : (th) => alpha(RED, th.palette.mode === "dark" ? 0.05 : 0.035),
                            }}
                          >
                            <Typography
                              sx={{
                                typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                                color: n
                                  ? (strong ? "common.white" : "text.primary")
                                  : alpha(RED, 0.85),
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

          {/* Guardrails */}
          <Box sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
            <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
                Guardrail coverage
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Every declared rule and whether any scenario tests it
              </Typography>
              <Box sx={{ flex: 1 }} />
              <Typography sx={{
                typography: "s2", fontWeight: 600, fontVariantNumeric: "tabular-nums",
                color: uncovered.length > 0 ? RED : "text.primary",
              }}>
                {rules.length - uncovered.length}/{rules.length}
              </Typography>
            </Stack>
            <Stack spacing={0.5}>
              {rules.map((r) => (
                <Stack key={r.rule} direction="row" alignItems="flex-start" spacing={1.25}>
                  <Box sx={{
                    width: 8, height: 8, borderRadius: "50%",
                    bgcolor: r.count ? GREEN : "transparent",
                    border: "1px solid",
                    borderColor: r.count ? GREEN : (t) => alpha(t.palette.text.primary, 0.4),
                    flexShrink: 0, mt: "5px",
                  }} />
                  <Typography sx={{
                    typography: "s2",
                    color: r.count ? "text.primary" : "text.subtitle",
                    flex: 1, minWidth: 0,
                  }}>
                    {r.rule}
                  </Typography>
                  <Typography noWrap sx={{
                    typography: "s3",
                    color: r.count ? "text.subtitle" : RED,
                    flexShrink: 0,
                  }}>
                    {r.count
                      ? `${r.count} scenario${r.count === 1 ? "" : "s"}`
                      : "no coverage"}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Collapse>
      </SectionCard>
    </Box>
  );
}

CoverageMatrix.propTypes = {
  env: PropTypes.object,
  scenarios: PropTypes.array.isRequired,
  defaultExpanded: PropTypes.bool,
};

const headerCellSx = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: 0.6,
  py: 0.75,
  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015),
};

function SummaryStat({ label, value, color }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.75}>
      <Typography sx={{ typography: "s3", color: "text.subtitle", fontSize: 11.5 }}>
        {label}
      </Typography>
      <Typography sx={{
        typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
        color: color || "text.primary",
      }}>
        {value}
      </Typography>
    </Stack>
  );
}
SummaryStat.propTypes = { label: PropTypes.string, value: PropTypes.string, color: PropTypes.string };

// Compact fill bar — density cue next to the fraction. Toned red → amber → green.
function MicroBar({ ratio }) {
  const pct = Math.max(0, Math.min(1, ratio || 0));
  return (
    <Box sx={{
      width: 44, height: 3, borderRadius: 999,
      bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.08),
      overflow: "hidden",
      flexShrink: 0,
    }}>
      <Box sx={{
        width: `${pct * 100}%`, height: "100%",
        bgcolor: toneColor(ratio),
        transition: "width 0.15s ease",
      }} />
    </Box>
  );
}
MicroBar.propTypes = { ratio: PropTypes.number };

// Traffic-light tone for a 0..1 ratio — one function so every place (fractions,
// micro-bars, header stats) tiers on the same thresholds.
function toneColor(ratio) {
  const r = Number(ratio) || 0;
  if (r < 0.34) return RED;
  if (r < 0.67) return AMBER;
  return GREEN;
}

function AxisPick({ label, value, onChange, exclude }) {
  return (
    <TextField
      select size="small" label={label} value={value}
      onChange={(e) => onChange(e.target.value)}
      sx={{ minWidth: 110, "& .MuiInputBase-input": { typography: "s2", py: 0.5, fontSize: 12 } }}
    >
      {PRD_AXES.filter((a) => a.id !== exclude).map((a) => (
        <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>{a.id} · {a.label}</MenuItem>
      ))}
    </TextField>
  );
}
AxisPick.propTypes = { label: PropTypes.string, value: PropTypes.string, onChange: PropTypes.func, exclude: PropTypes.string };
