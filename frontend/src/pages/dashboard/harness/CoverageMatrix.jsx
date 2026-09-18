import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Chip, MenuItem, Stack, TextField, Tooltip, Typography } from "@mui/material";

// Coverage.
//
// A scenario count is not coverage. This cross-tabulates the suite so an empty cell is the finding:
// "nothing here puts a prompt injection against a guest caller" is the sentence worth putting in
// front of someone, and a total of fifty never says it.
//
// The axes are the plan's own, read from each scenario's `coverage` coordinate, so a browser or
// computer-use agent gets its own axes here without this file knowing their names.
//
// Two bands, following the studio design: covered cells sit on a green ramp from a soft tint at one
// scenario to a saturated fill at the busiest cell, empty cells sit on a red tint with a dashed
// border. Green gives "empty = red = gap" a natural opposite.

const GREEN = "#16A34A";
const RED = "#DC2626";

const levelsOf = (scenarios, axis) => [
  ...new Set(
    scenarios
      .map((one) => String((one?.coverage || {})[axis] ?? "").trim())
      .filter(Boolean),
  ),
].sort();

export default function CoverageMatrix({ scenarios, coverage }) {
  const rows = useMemo(() => (Array.isArray(scenarios) ? scenarios : []), [scenarios]);
  const axes = useMemo(
    () =>
      [
        ...new Set(
          rows.flatMap((one) => Object.keys(one?.coverage || {})).filter(Boolean),
        ),
      ].sort(),
    [rows],
  );

  const [rowAxis, setRowAxis] = useState(axes[0] || "");
  const [colAxis, setColAxis] = useState(axes[1] || axes[0] || "");

  const matrix = useMemo(() => {
    if (!rowAxis || !colAxis) return null;
    const rowKeys = levelsOf(rows, rowAxis);
    const colKeys = levelsOf(rows, colAxis);
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
  }, [rows, rowAxis, colAxis]);

  if (!axes.length || !matrix) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenario carries a coordinate, so there is nothing to cross-tabulate.
      </Typography>
    );
  }

  // Levels the plan said it would cover and no scenario ever used. An empty cell is a gap you can
  // see; an unused level is a gap the matrix cannot show, because the column is simply absent.
  const unused = Object.entries(coverage?.axes || {}).flatMap(([axis, body]) =>
    (body?.unused || []).map((level) => ({ axis, level })),
  );

  // The weakest pair, straight from the report. A person opening this wants one sentence about
  // whether the suite is thin, and "22 of 121 combinations" is that sentence; the matrix below is
  // for working out which 99.
  const weakest = Object.entries(coverage?.pairs || {})
    .filter(([, body]) => body?.possible)
    .sort((a, b) => (a[1].share ?? 1) - (b[1].share ?? 1))[0];

  // Named gaps, not just empty squares. Scanning a grid for blanks is work; reading "nothing puts
  // prompt_injection against cancel_ride" is not.
  const named = matrix.rowKeys
    .flatMap((r) => matrix.colKeys.map((c) => ({ r, c, n: matrix.at(r, c) })))
    .filter((cell) => cell.n === 0);

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

      <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
        <Typography variant="body2" sx={{ flex: 1, minWidth: 220 }}>
          {rows.length} scenarios across {matrix.rowKeys.length} x {matrix.colKeys.length}
          {matrix.empty ? ` — ${matrix.empty} empty cells are the gaps` : " — every cell covered"}
        </Typography>
        <TextField
          select size="small" label="Rows" value={rowAxis}
          onChange={(event) => setRowAxis(event.target.value)} sx={{ minWidth: 150 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis} disabled={axis === colAxis}>{axis}</MenuItem>
          ))}
        </TextField>
        <TextField
          select size="small" label="Columns" value={colAxis}
          onChange={(event) => setColAxis(event.target.value)} sx={{ minWidth: 150 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis} disabled={axis === rowAxis}>{axis}</MenuItem>
          ))}
        </TextField>
      </Stack>

      <Box sx={{ overflowX: "auto" }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `minmax(140px, max-content) repeat(${matrix.colKeys.length}, minmax(84px, 1fr))`,
            gap: 0.5,
            minWidth: "max-content",
          }}
        >
          <Box />
          {matrix.colKeys.map((col) => (
            <Typography
              key={col}
              variant="caption"
              sx={{ fontWeight: 700, color: "text.secondary", textAlign: "center", pb: 0.5 }}
            >
              {col}
            </Typography>
          ))}

          {matrix.rowKeys.map((row) => (
            <Box key={row} sx={{ display: "contents" }}>
              <Typography variant="body2" sx={{ fontWeight: 600, alignSelf: "center", pr: 1.5 }}>
                {row}
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
                        : `Nothing puts ${row} against ${col} — worth generating some`
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

      {Boolean(named.length) && (
        <Stack spacing={0.5}>
          <Typography variant="caption" color="text.secondary">
            Nothing covers these {named.length} combinations
          </Typography>
          {named.slice(0, 6).map((cell) => (
            <Typography key={`${cell.r}|${cell.c}`} variant="body2" color="text.secondary">
              • {rowAxis} <strong>{cell.r}</strong> against {colAxis} <strong>{cell.c}</strong>
            </Typography>
          ))}
          {named.length > 6 && (
            <Typography variant="caption" color="text.secondary">
              and {named.length - 6} more, shown as dashed cells below
            </Typography>
          )}
        </Stack>
      )}

      {Boolean(unused.length) && (
        <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
          <Typography variant="caption" color="text.secondary">
            Planned, never written:
          </Typography>
          {unused.map(({ axis, level }) => (
            <Tooltip key={`${axis}:${level}`} title={`${axis} level the plan dealt and no scenario used`} arrow>
              <Chip size="small" variant="outlined" color="warning" label={`${axis}: ${level}`} />
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
  }),
};
