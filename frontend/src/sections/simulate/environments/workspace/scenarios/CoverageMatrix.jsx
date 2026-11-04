import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, TextField, MenuItem, Tooltip, CircularProgress,
  Table, TableBody, TableHead, TableRow, TableCell, Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SideDrawer from "../../components/SideDrawer";
import { ROW_HEIGHT } from "src/theme/sizes";
import { useScenarioCoverage } from "src/api/simulate-environments/scenariosHooks";
import { scenarioToolbarButtonSx } from "./scenarios.constants";


// The five rare-catastrophic overlays that must be present regardless of
// sampling (server naming). "Forced" in the header summary counts these.
const FORCED_OVERLAYS = [
  { id: "destructive", label: "Destructive / irreversible" },
  { id: "minor_vulnerable", label: "Minor / vulnerable" },
  { id: "emergency_crisis", label: "Emergency / crisis" },
  { id: "privacy_pii", label: "PII / privacy" },
  { id: "prompt_injection", label: "Prompt-injection" },
];

// A coverage level / axis name is stored snake_case; show it as spaced words.
const humanize = (s) =>
  String(s ?? "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());

// Coverage — the server cross-tab.
//
// A scenario count is not coverage. Every scenario is a coordinate over eight
// coverage axes; this panel reports, for each axis, how many distinct levels the
// suite actually varies, and for a chosen pair of axes which combinations are
// covered — the empty cells are the finding. The grid does not change with the
// page but does change with the filter, so it reads the same search + filters as
// the list. It lives in the list toolbar as one button so the table keeps the
// height: hover names the three summary numbers (Axes / Pairs / Forced), and a
// click opens the per-axis table, the forced overlays and the pairwise heatmap
// in a right-side drawer.
export default function CoverageMatrix({ jobId, search, filters, defaultOpen = false }) {
  // Left undefined until the user picks, so the server default (task × overlay)
  // drives the initial grid.
  const [rowAxis, setRowAxis] = useState(undefined);
  const [colAxis, setColAxis] = useState(undefined);
  const [open, setOpen] = useState(defaultOpen);

  const { data, isError, isPending, refetch } = useScenarioCoverage(jobId, { search, filters, rowAxis, colAxis });

  const axes = data?.axes ?? [];
  const perAxis = useMemo(() => data?.per_axis ?? [], [data]);
  const rows = data?.rows ?? [];
  const columns = data?.columns ?? [];
  // The user's pick wins immediately; the server's default only stands until one
  // is chosen. (keepPreviousData holds a stale response's axis through a refetch,
  // so preferring `data` here would snap the dropdown back mid-fetch.)
  const activeRowAxis = rowAxis ?? data?.row_axis ?? "";
  const activeColAxis = colAxis ?? data?.col_axis ?? "";
  // Axis and level names are served with the grid; humanize only covers a missing one.
  const axisLabel = (axis) => data?.axis_labels?.[axis] ?? humanize(axis);
  const levelLabel = (level) => data?.level_labels?.[level] ?? humanize(level);

  // A count lookup for the chosen pair, and the busiest cell for the ramp.
  const { cellAt, maxCell } = useMemo(() => {
    const map = new Map();
    let max = 0;
    for (const c of data?.cells ?? []) {
      map.set(`${c.row}|${c.column}`, c.count);
      if (c.count > max) max = c.count;
    }
    return { cellAt: (r, c) => map.get(`${r}|${c}`) ?? 0, maxCell: max };
  }, [data]);

  const scenarioCount = perAxis[0]?.scenarios ?? 0;

  // Axes summary: how many axes are varied at all (more than one level) — a
  // different question from the cross-tab, and one the server data supports
  // without a universe size.
  const variedAxes = perAxis.filter((a) => a.levels > 1).length;
  const axesRatio = perAxis.length ? variedAxes / perAxis.length : 0;

  // Pairs summary: of the observed row × column grid, the fraction of cells that
  // hold at least one scenario — a real ratio over the levels the suite covers,
  // not an invented universe.
  const totalCells = rows.length * columns.length;
  const filledCells = (data?.cells ?? []).filter((c) => c.count > 0).length;
  const pairsRatio = totalCells ? filledCells / totalCells : 0;

  // Forced: the five rare-catastrophic overlays present in the overlay axis.
  const overlayCounts = perAxis.find((a) => a.axis === "overlay")?.counts ?? {};
  const forcedPresent = FORCED_OVERLAYS.filter((f) => (overlayCounts[f.id] ?? 0) > 0);


  const forcedRatio = forcedPresent.length / FORCED_OVERLAYS.length;
  const pairsPct = Math.round(pairsRatio * 100);
  const summary = `Axes ${variedAxes}/${perAxis.length}, Pairs ${pairsPct}%, Forced ${forcedPresent.length}/${FORCED_OVERLAYS.length}`;
  const close = () => setOpen(false);

  const helpContent = (
    <Box sx={{ p: 0.5, maxWidth: 380 }}>
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightMedium", mb: 0.5 }}>
        What this panel answers
      </Typography>
      <Typography sx={{ typography: "s3", mb: 1.5 }}>
        Not &quot;how many tests do we have&quot;, but{" "}
        <Box component="span" sx={{ fontWeight: "fontWeightMedium" }}>
          &quot;what kinds of situations did we forget to test?&quot;
        </Box>
      </Typography>

      <Typography sx={{ typography: "s3", fontWeight: "fontWeightMedium", mb: 0.75 }}>
        Three numbers at a glance
      </Typography>
      <Typography component="ul" sx={{ typography: "s3", pl: 2, mb: 1.5 }}>
        <li><Box component="span" sx={{ fontWeight: "fontWeightMedium" }}>Axes</Box> - how many of the coverage axes the suite actually varies (more than one level).</li>
        <li><Box component="span" sx={{ fontWeight: "fontWeightMedium" }}>Pairs</Box> - of the two axes shown below, what fraction of the observed combinations are covered.</li>
        <li><Box component="span" sx={{ fontWeight: "fontWeightMedium" }}>Forced</Box> - how many of the five dangerous must-have overlays are present.</li>
      </Typography>

      <Typography sx={{ typography: "s3" }}>
        Empty red cells in the grid below are the actual gaps. They name a combination nothing in the suite tests yet.
      </Typography>
    </Box>
  );


  // A failed request must read as an error and a first load as loading, never
  // as a zero-coverage summary (which would falsely say the suite covers nothing).
  let label = `Coverage: ${summary}`;
  let mark = <Iconify icon="solar:chart-square-linear" width={14} />;
  if (isError) {
    label = "Coverage couldn't load";
    mark = <Iconify icon="solar:danger-triangle-linear" width={14} sx={{ color: "status.danger.solid" }} />;
  } else if (isPending) {
    label = "Loading coverage";
    mark = <CircularProgress size={10} thickness={6} sx={{ color: "text.subtitle" }} />;
  }

  return (
    <>
      {/* Always mounted: toggling `show` swaps the wrapper and remounts the
          button, so the drawer couldn't hand focus back to it on close. An
          empty title keeps the tooltip quiet instead. */}
      <CustomTooltip
        show
        arrow
        size="small"
        title={open || isError || isPending ? "" : summary.replaceAll(", ", " · ")}
      >
        <Button
          aria-label={label}
          aria-busy={isPending || undefined}
          disabled={isPending}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(true)}
          variant="outlined"
          size="small"
          color="inherit"
          startIcon={mark}
          sx={scenarioToolbarButtonSx}
        >
          Coverage
        </Button>
      </CustomTooltip>
      {/* Wide enough for the pairwise grid; the body scrolls under a fixed header. */}
      <SideDrawer open={open} onClose={close} width={880}>
        {isError ? (
          <Stack alignItems="center" justifyContent="center" spacing={1} sx={{ height: "100%", px: 2 }}>
            <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
              Couldn&apos;t load coverage.
            </Typography>
            <Button size="small" variant="outlined" onClick={() => refetch()}>
              Try again
            </Button>
          </Stack>
        ) : (
          <Stack sx={{ height: "100%" }}>
            <Box sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
              <Box>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography sx={{ typography: "m1", fontWeight: "fontWeightMedium" }}>Coverage</Typography>
                  <Tooltip
                    arrow
                    title={helpContent}
                    placement="right-start"
                    enterDelay={80}
                    leaveDelay={100}
                    slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}
                  >
                    <Box
                      component="span"
                      sx={{ display: "inline-flex", cursor: "help", color: "text.subtitle", "&:hover": { color: "text.primary" } }}
                      aria-label="What is coverage?"
                    >
                      <Iconify icon="solar:info-circle-linear" width={15} />
                    </Box>
                  </Tooltip>
                </Stack>
                <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
                  {`${scenarioCount} scenarios · what kinds of situations did we forget to test?`}
                </Typography>
              </Box>
              <Stack direction="row" spacing={3} sx={{ mt: 1.5 }}>
                <SummaryStat label="Axes" value={`${variedAxes}/${perAxis.length}`} color={toneColor(axesRatio)} />
                <SummaryStat label="Pairs" value={`${pairsPct}%`} color={toneColor(pairsRatio)} />
                <SummaryStat
                  label="Forced"
                  value={`${forcedPresent.length}/${FORCED_OVERLAYS.length}`}
                  color={toneColor(forcedRatio)}
                />
              </Stack>
            </Box>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              {/* Per-axis table — how many distinct levels each axis varied, and the
                  levels themselves with their counts. */}
              <Table size="small" sx={{
                "& .MuiTableCell-root": {
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  py: 0,
                },
              }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={headerCellSx} width={220}>Axis</TableCell>
                    <TableCell sx={headerCellSx} width={110} align="right">Levels</TableCell>
                    <TableCell sx={headerCellSx}>Covered levels</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {perAxis.map((a) => {
                    const levelNames = Object.keys(a.counts || {});
                    return (
                      <TableRow key={a.axis} hover sx={{ height: ROW_HEIGHT.default }}>
                        <TableCell>
                          <Typography sx={{ typography: "s1", color: "text.primary" }}>
                            {axisLabel(a.axis)}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography sx={{
                            typography: "s1", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
                            color: a.levels > 1 ? "text.primary" : "status.danger.fg",
                            minWidth: 36, textAlign: "right",
                          }}>
                            {a.levels}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography
                            sx={{ typography: "s3", color: "text.subtitle" }}
                            noWrap
                            title={levelNames.map((lvl) => `${levelLabel(lvl)} (${a.counts[lvl]})`).join(" · ")}
                          >
                            {levelNames.map((lvl) => `${levelLabel(lvl)} · ${a.counts[lvl]}`).join("   ")}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>

              {/* Forced overlays */}
              <Box sx={{ px: 3, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
                <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
                  <Typography sx={{ typography: "m3", fontWeight: "fontWeightMedium", color: "text.primary" }}>
                    Forced overlays
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    Must be present regardless of sampling
                  </Typography>
                  <Box sx={{ flex: 1 }} />
                  <Typography sx={{
                    typography: "s1", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
                    color: forcedPresent.length < FORCED_OVERLAYS.length ? "status.danger.fg" : "text.primary",
                  }}>
                    {forcedPresent.length}/{FORCED_OVERLAYS.length}
                  </Typography>
                </Stack>
                <Box sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", md: "1fr 1fr 1fr" },
                  rowGap: 0.75, columnGap: 3,
                }}>
                  {FORCED_OVERLAYS.map((f) => {
                    const present = (overlayCounts[f.id] ?? 0) > 0;
                    return (
                      <Stack key={f.id} direction="row" alignItems="center" spacing={1}>
                        <Box sx={{
                          width: 8, height: 8, borderRadius: "50%",
                          bgcolor: present ? "status.success.solid" : "transparent",
                          border: "1px solid",
                          borderColor: present ? "status.success.solid" : "text.disabled",
                          flexShrink: 0,
                        }} />
                        <Typography sx={{
                          typography: "s1",
                          color: present ? "text.primary" : "text.subtitle",
                        }}>
                          {f.label}
                        </Typography>
                      </Stack>
                    );
                  })}
                </Box>
              </Box>

              {/* Pairwise heatmap */}
              <Box sx={{ px: 3, py: 1.5 }}>
                <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
                  <Typography sx={{ typography: "m3", fontWeight: "fontWeightMedium", color: "text.primary" }}>
                    Pairwise · {axisLabel(activeRowAxis)} × {axisLabel(activeColAxis)}
                  </Typography>
                  <Box sx={{ flex: 1 }} />
                  <AxisPick label="Rows" value={activeRowAxis} onChange={setRowAxis} options={axes} exclude={activeColAxis} labelOf={axisLabel} />
                  <AxisPick label="Columns" value={activeColAxis} onChange={setColAxis} options={axes} exclude={activeRowAxis} labelOf={axisLabel} />
                </Stack>
                <Box sx={{ overflowX: "auto" }}>
                  <Box
                    sx={{
                      display: "grid",
                      gridTemplateColumns: `minmax(140px, max-content) repeat(${columns.length}, minmax(72px, 1fr))`,
                      gap: 0.5, minWidth: "max-content",
                    }}
                  >
                    <Box />
                    {columns.map((c) => (
                      <Typography
                        key={c}
                        sx={{
                          typography: "s3", fontWeight: "fontWeightMedium", color: "text.subtitle",
                          textAlign: "center", pb: 0.5,
                        }}
                      >
                        {levelLabel(c)}
                      </Typography>
                    ))}

                    {rows.map((r) => (
                      <Box key={r} sx={{ display: "contents" }}>
                        <Typography sx={{
                          typography: "s1", fontWeight: "fontWeightMedium", alignSelf: "center",
                          pr: 1.5,
                        }}>
                          {levelLabel(r)}
                        </Typography>
                        {columns.map((c) => {
                          const n = cellAt(r, c);
                          const t = maxCell > 1 ? (n - 1) / (maxCell - 1) : (n > 0 ? 1 : 0);
                          const opacity = n ? 0.08 + 0.22 * t : 0;
                          return (
                            <Tooltip
                              key={c} arrow
                              title={n
                                ? `${n} scenario${n === 1 ? "" : "s"} - ${levelLabel(r)} × ${levelLabel(c)}`
                                : `Empty - ${levelLabel(r)} × ${levelLabel(c)}`}
                            >
                              <Box
                                sx={{
                                  height: 40, borderRadius: 1, display: "grid", placeItems: "center",
                                  border: "1px solid",
                                  // Covered cells ramp one data-palette hue by count;
                                  // a gap is a status, so it takes the danger tokens.
                                  borderColor: n
                                    ? (th) => alpha(th.palette.data[0], 0.12 + 0.2 * t)
                                    : "status.danger.border",
                                  borderStyle: n ? "solid" : "dashed",
                                  bgcolor: n
                                    ? (th) => alpha(th.palette.data[0], opacity)
                                    : "status.danger.bg",
                                }}
                              >
                                <Typography
                                  sx={{
                                    typography: "s1", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
                                    color: n ? "text.primary" : "status.danger.fg",
                                  }}
                                >
                                  {n || "-"}
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
            </Box>
          </Stack>
        )}
      </SideDrawer>
    </>
  );
}

CoverageMatrix.propTypes = {
  // The harness job id — coverage reads the filtered suite for this job.
  jobId: PropTypes.string,
  // The same search + object-style filters the list sends; the grid changes with
  // the filter but not the page.
  search: PropTypes.string,
  filters: PropTypes.object,
  defaultOpen: PropTypes.bool,
};

const headerCellSx = {
  typography: "s3",
  fontWeight: "fontWeightMedium",
  color: "text.subtitle",
  py: 0.75,
};

function SummaryStat({ label, value, color }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={0.75}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Typography sx={{
        typography: "s1", fontWeight: "fontWeightMedium", fontVariantNumeric: "tabular-nums",
        color: color || "text.primary",
      }}>
        {value}
      </Typography>
    </Stack>
  );
}
SummaryStat.propTypes = { label: PropTypes.string, value: PropTypes.string, color: PropTypes.string };

// Traffic-light tone for a 0..1 ratio — one function so every place tiers on the
// same thresholds.
function toneColor(ratio) {
  const r = Number(ratio) || 0;
  if (r < 0.34) return "status.danger.fg";
  if (r < 0.67) return "status.warning.fg";
  return "status.success.fg";
}

function AxisPick({ label, value, onChange, options, exclude, labelOf = humanize }) {
  return (
    <TextField
      select size="small" label={label} value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      sx={{ minWidth: 140 }}
    >
      {options.filter((a) => a !== exclude).map((a) => (
        <MenuItem key={a} value={a}>{labelOf(a)}</MenuItem>
      ))}
    </TextField>
  );
}
AxisPick.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.arrayOf(PropTypes.string),
  exclude: PropTypes.string,
  labelOf: PropTypes.func,
};
