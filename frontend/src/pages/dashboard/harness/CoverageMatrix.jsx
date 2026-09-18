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
  //
  // Ordered by how much each one tells you. A level that appears nowhere in the pairing is a
  // bigger finding than one missing cell, and it is also the one a reader would otherwise have to
  // infer from a whole empty row. Single cells come last and are taken one per level, because six
  // gaps that all name the same overlay read as one fact repeated rather than six.
  const empties = matrix.rowKeys
    .flatMap((r) => matrix.colKeys.map((c) => ({ r, c })))
    .filter((cell) => !matrix.at(cell.r, cell.c));
  const barrenRows = matrix.rowKeys.filter((r) => !matrix.colKeys.some((c) => matrix.at(r, c)));
  const barrenCols = matrix.colKeys.filter((c) => !matrix.rowKeys.some((r) => matrix.at(r, c)));
  const spread = [];
  const seenRows = new Set();
  const seenCols = new Set();
  // First pass takes a gap whose level is new on both axes, so six lines name twelve things rather
  // than one column six times over. The second fills up from whatever is left.
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
                  • nothing tests {gap.axis} <strong>{gap.level}</strong> against any{" "}
                  {gap.axis === rowAxis ? colAxis : rowAxis}
                </>
              ) : (
                <>
                  • {rowAxis} <strong>{gap.r}</strong> against {colAxis} <strong>{gap.c}</strong>
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

      {/* A plan with many levels makes a grid taller than the window, and this panel sits above
          the suite: without a ceiling the list underneath becomes unreachable, which is the whole
          reason coverage was moved to the top. Bounded and scrolled in place, with the labels
          pinned so they survive the scroll in both directions. */}
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
              {col}
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
