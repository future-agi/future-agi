import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Chip, MenuItem, Stack, TextField, Tooltip, Typography } from "@mui/material";

// Cross-tabulates a suite over the axes the plan declared, so an empty cell reads as a gap.
// Axis names come from each scenario's `coverage` coordinate and are never known here. What they
// are CALLED comes from the coverage report's own labels, so a reader sees "What makes it hard"
// rather than `overlay`, and the words cannot drift from the harness that deals the coordinates.

const GREEN = "#16A34A";
const RED = "#DC2626";

const titled = (raw) =>
  String(raw || "")
    .replace(/[_-]+/g, " ")
    .replace(/^./, (first) => first.toUpperCase());

// The dealt levels and the occupied ones are read at different moments, so take the union.
const levelsOf = (scenarios, axis, coverage) => {
  const fromScenarios = scenarios
    .map((one) => String((one?.coverage || {})[axis] ?? "").trim())
    .filter(Boolean);
  const reported = coverage?.axes?.[axis] || {};
  const fromReport = [
    ...Object.keys(reported.counts || {}),
    ...(reported.unused || []),
  ].map((one) => String(one ?? "").trim());
  return [...new Set([...fromScenarios, ...fromReport].filter(Boolean))].sort();
};

export default function CoverageMatrix({ scenarios, coverage }) {
  const rows = useMemo(() => (Array.isArray(scenarios) ? scenarios : []), [scenarios]);
  const axes = useMemo(
    () =>
      [
        ...new Set([
          ...rows.flatMap((one) => Object.keys(one?.coverage || {})),
          ...Object.keys(coverage?.axes || {}),
        ]),
      ]
        .filter(Boolean)
        .sort(),
    [rows, coverage],
  );

  const [rowAxis, setRowAxis] = useState(axes[0] || "");
  const [colAxis, setColAxis] = useState(axes[1] || axes[0] || "");
  const axisLabel = (axis) => coverage?.labels?.axes?.[axis] || titled(axis);
  const levelLabel = (level) => coverage?.labels?.levels?.[level] || titled(level);

  const matrix = useMemo(() => {
    if (!rowAxis || !colAxis) return null;
    const rowKeys = levelsOf(rows, rowAxis, coverage);
    const colKeys = levelsOf(rows, colAxis, coverage);
    const counts = {};
    rows.forEach((one) => {
      const r = String((one?.coverage || {})[rowAxis] ?? "").trim();
      const c = String((one?.coverage || {})[colAxis] ?? "").trim();
      if (!r || !c) return;
      const key = `${r}|${c}`;
      counts[key] = (counts[key] || 0) + 1;
    });
    const cells = rowKeys.flatMap((r) => colKeys.map((c) => counts[`${r}|${c}`] || 0));
    return {
      rowKeys,
      colKeys,
      at: (r, c) => counts[`${r}|${c}`] || 0,
      max: Math.max(1, ...cells),
      empty: cells.filter((n) => n === 0).length,
      total: cells.length,
    };
  }, [rows, rowAxis, colAxis, coverage]);

  if (!axes.length || !matrix) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenario carries a coordinate, so there is nothing to cross-tabulate.
      </Typography>
    );
  }

  // A level no scenario used has no column, so the matrix cannot show it as a gap.
  const unused = Object.entries(coverage?.axes || {}).flatMap(([axis, body]) =>
    (body?.unused || []).map((level) => ({ axis, level })),
  );

  const weakest = Object.entries(coverage?.pairs || {})
    .filter(([, body]) => body?.possible)
    .sort((a, b) => (a[1].share ?? 1) - (b[1].share ?? 1))[0];

  // Ranked: a level absent from the whole pairing outranks a single missing cell.
  const empties = matrix.rowKeys
    .flatMap((r) => matrix.colKeys.map((c) => ({ r, c })))
    .filter((cell) => !matrix.at(cell.r, cell.c));
  const barrenRows = matrix.rowKeys.filter((r) => !matrix.colKeys.some((c) => matrix.at(r, c)));
  const barrenCols = matrix.colKeys.filter((c) => !matrix.rowKeys.some((r) => matrix.at(r, c)));
  const spread = [];
  const seenRows = new Set();
  const seenCols = new Set();
  // First pass takes gaps new on both axes so the list does not repeat one level.
  for (const pass of [0, 1]) {
    for (const cell of empties) {
      if (barrenRows.includes(cell.r) || barrenCols.includes(cell.c)) continue;
      if (!pass && (seenRows.has(cell.r) || seenCols.has(cell.c))) continue;
      if (spread.some((one) => one.r === cell.r && one.c === cell.c)) continue;
      seenRows.add(cell.r);
      seenCols.add(cell.c);
      spread.push(cell);
    }
  }
  const named = [
    ...barrenRows.map((r) => ({ whole: true, axis: rowAxis, level: r })),
    ...barrenCols.map((c) => ({ whole: true, axis: colAxis, level: c })),
    ...spread,
  ];

  return (
    <Stack spacing={2}>
      {weakest && (
        <Typography variant="body2">
          Thinnest pairing is <strong>{weakest[0]}</strong>, covering{" "}
          <strong>
            {weakest[1].covered} of {weakest[1].possible}
          </strong>{" "}
          combinations
          {weakest[1].masked ? ` (${weakest[1].masked} masked as unreachable)` : ""}.
        </Typography>
      )}

      {Boolean(named.length) && (
        <Stack spacing={0.5}>
          <Typography variant="caption" color="text.secondary">
            Nothing covers these {empties.length} combinations
          </Typography>
          {named.slice(0, 6).map((gap) => (
            <Typography
              key={gap.whole ? `${gap.axis}:${gap.level}` : `${gap.r}|${gap.c}`}
              variant="body2"
              color="text.secondary"
            >
              {gap.whole ? (
                <>
                  • nothing tests {axisLabel(gap.axis)}{" "}
                  <strong>{levelLabel(gap.level)}</strong> against any{" "}
                  {axisLabel(gap.axis === rowAxis ? colAxis : rowAxis)}
                </>
              ) : (
                <>
                  • {axisLabel(rowAxis)} <strong>{levelLabel(gap.r)}</strong> against{" "}
                  {axisLabel(colAxis)} <strong>{levelLabel(gap.c)}</strong>
                </>
              )}
            </Typography>
          ))}
          {named.length > 6 && (
            <Typography variant="caption" color="text.secondary">
              and {named.length - 6} more, shown as dashed cells below
            </Typography>
          )}
        </Stack>
      )}

      <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
        <Typography variant="body2" sx={{ flex: 1, minWidth: 220 }}>
          {rows.length} scenarios across {matrix.rowKeys.length} x {matrix.colKeys.length}
          {matrix.empty ? `, ${matrix.empty} of them empty` : ", every cell covered"}
        </Typography>
        <TextField
          select size="small" label="Rows" value={rowAxis}
          onChange={(event) => setRowAxis(event.target.value)} sx={{ minWidth: 150 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis} disabled={axis === colAxis}>
              {axisLabel(axis)}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select size="small" label="Columns" value={colAxis}
          onChange={(event) => setColAxis(event.target.value)} sx={{ minWidth: 150 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis} disabled={axis === rowAxis}>
              {axisLabel(axis)}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      {/* Bounded: this panel sits above an unbounded list, so the grid may not grow without limit. */}
      <Box sx={{ overflow: "auto", maxHeight: 420, border: 1, borderColor: "divider", borderRadius: 1, p: 1 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `minmax(140px, max-content) repeat(${matrix.colKeys.length}, minmax(84px, 1fr))`,
            gap: 0.5,
            minWidth: "max-content",
          }}
        >
          <Box sx={{ position: "sticky", top: 0, left: 0, zIndex: 3, bgcolor: "background.default" }} />
          {matrix.colKeys.map((col) => (
            <Typography
              key={col}
              variant="caption"
              sx={{
                fontWeight: 700,
                color: "text.secondary",
                textAlign: "center",
                pb: 0.5,
                position: "sticky",
                top: 0,
                zIndex: 2,
                bgcolor: "background.default",
              }}
            >
              {levelLabel(col)}
            </Typography>
          ))}

          {matrix.rowKeys.map((row) => (
            <Box key={row} sx={{ display: "contents" }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  alignSelf: "center",
                  pr: 1.5,
                  position: "sticky",
                  left: 0,
                  zIndex: 1,
                  bgcolor: "background.default",
                }}
              >
                {levelLabel(row)}
              </Typography>
              {matrix.colKeys.map((col) => {
                const n = matrix.at(row, col);
                const t = matrix.max > 1 ? (n - 1) / (matrix.max - 1) : n > 0 ? 1 : 0;
                const strong = n && n / matrix.max >= 0.85;
                return (
                  <Tooltip
                    key={col}
                    arrow
                    title={
                      n
                        ? `${n} scenario${n === 1 ? "" : "s"} put ${row} against ${col}`
                        : `Nothing puts ${row} against ${col}, so this combination is untested`
                    }
                  >
                    <Box
                      sx={{
                        height: 44,
                        borderRadius: 1,
                        display: "grid",
                        placeItems: "center",
                        border: "1px solid",
                        borderStyle: n ? "solid" : "dashed",
                        borderColor: n ? alpha(GREEN, 0.12 + 0.2 * t) : alpha(RED, 0.28),
                        bgcolor: n
                          ? alpha(GREEN, 0.08 + 0.22 * t)
                          : (theme) =>
                              alpha(RED, theme.palette.mode === "dark" ? 0.05 : 0.035),
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 700,
                          fontVariantNumeric: "tabular-nums",
                          color: n ? (strong ? "#fff" : "text.primary") : alpha(RED, 0.85),
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

      {Boolean(unused.length) && (
        <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
          <Typography variant="caption" color="text.secondary">
            Planned, never written:
          </Typography>
          {unused.map(({ axis, level }) => (
            <Tooltip
              key={`${axis}:${level}`}
              title={`${axisLabel(axis)} level the plan dealt and no scenario used`}
              arrow
            >
              <Chip
                size="small"
                variant="outlined"
                color="warning"
                label={`${axisLabel(axis)}: ${levelLabel(level)}`}
              />
            </Tooltip>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

CoverageMatrix.propTypes = {
  scenarios: PropTypes.arrayOf(PropTypes.object),
  coverage: PropTypes.shape({
    axes: PropTypes.object,
    pairs: PropTypes.object,
    labels: PropTypes.shape({
      axes: PropTypes.object,
      levels: PropTypes.object,
    }),
  }),
};
