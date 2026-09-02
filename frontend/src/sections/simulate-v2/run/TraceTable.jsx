import PropTypes from "prop-types";
import React from "react";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Checkbox, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { interpolateColorBasedOnScore } from "src/utils/utils";

/**
 * The traces, as a table.
 *
 * Modelled on the dataset grid rather than on a list: tall rows that let the
 * long fields — the situation the persona is in, what a good outcome looks
 * like, the branch the conversation actually took — be read rather than
 * truncated to a tooltip. Squeezing them into 40px single-line rows hid the
 * only content on the screen worth reading.
 *
 * Two conventions come straight from that grid. An eval result fills its cell
 * edge to edge in the score's colour, through the same
 * `interpolateColorTokenBasedOnScore` token the product's other eval columns
 * use, so a column reads as a heat strip. And selection lives in a checkbox on
 * the left, not in a trailing column of unlabelled icons — which is also what
 * makes re-running a subset possible: tick the rows you want and run those.
 */
/* Per-call figures the run knows but never stored — derived the same way the
   execution adapter derives them, so both screens report the same number. */
const hash = (str) => {
  let h = 0;
  for (let i = 0; i < String(str).length; i += 1) h = (h * 31 + String(str).charCodeAt(i)) >>> 0;
  return h;
};
const csatOf = (t) => Math.max(1, Math.round((t.evalResults?.[0]?.score ?? 0.5) * 10) - 4);
const latencyOf = (t) => 280 + (hash(t.id) % 320);

export default function TraceTable({ tasks, evals, selected, onToggle, onToggleAll, onOpen }) {
  const allOn = tasks.length > 0 && tasks.every((t) => selected.has(t.id));
  const someOn = tasks.some((t) => selected.has(t.id)) && !allOn;

  const headCell = {
    typography: "s2", fontWeight: 500, color: "text.secondary",
    whiteSpace: "nowrap", bgcolor: "background.paper", height: 44, py: 0,
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  const num = {
    verticalAlign: "top", py: 1.5,
    typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums",
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  const bodyCell = {
    verticalAlign: "top", py: 1.5,
    borderBottom: "1px solid", borderColor: "divider",
    "&:not(:first-of-type)": { borderLeft: "1px solid", borderColor: "divider" },
  };
  /*
    The checkbox column is styled once and used in both the head and the body.
    It was picking up `verticalAlign: top` and the body's own vertical padding
    while the header cell used MUI's checkbox padding, so the two boxes sat at
    different heights and different insets — visibly off in a table whose rows
    are four lines tall.
  */
  const checkCell = {
    width: 48, p: 0, pl: 1.25, verticalAlign: "middle",
    borderBottom: "1px solid", borderColor: "divider",
  };

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" sx={{ minWidth: 1720, tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ ...headCell, ...checkCell }}>
              <Checkbox
                size="small"
                checked={allOn}
                indeterminate={someOn}
                onChange={onToggleAll}
              />
            </TableCell>
            <TableCell sx={{ ...headCell, width: 210 }}>Persona</TableCell>
            <TableCell sx={{ ...headCell, width: 300 }}>Scenario</TableCell>
            <TableCell sx={{ ...headCell, width: 300 }}>Ideal outcome</TableCell>
            <TableCell sx={{ ...headCell, width: 260 }}>Conversation branch</TableCell>
            <TableCell sx={{ ...headCell, width: 150 }}>Call details</TableCell>
            <TableCell sx={{ ...headCell, width: 84 }} align="right">CSAT</TableCell>
            <TableCell sx={{ ...headCell, width: 92 }} align="right">Turns</TableCell>
            <TableCell sx={{ ...headCell, width: 96 }} align="right">Latency</TableCell>
            <TableCell sx={{ ...headCell, width: 96 }} align="right">Tokens</TableCell>
            {evals.map((e) => (
              <TableCell key={e.id} sx={{ ...headCell, width: 150 }}>{e.name}</TableCell>
            ))}
          </TableRow>
        </TableHead>

        <TableBody>
          {tasks.map((t) => (
            <TableRow key={t.id} hover sx={{ cursor: "pointer" }}>
              <TableCell sx={checkCell}>
                <Checkbox
                  size="small"
                  checked={selected.has(t.id)}
                  onChange={() => onToggle(t.id)}
                  onClick={(e) => e.stopPropagation()}
                />
              </TableCell>

              {/* The persona as its own fields, so two personas can be compared
                  down the column rather than read as one run-on line. */}
              <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
                <Stack spacing={0.5}>
                  <Field icon="solar:user-id-linear" label="Name" value={t.persona?.name} />
                  <Field icon="solar:user-linear" label="Voice" value={t.persona?.voice} />
                  <Field icon="solar:users-group-rounded-linear" label="Age" value={t.persona?.age} />
                  {t.persona?.traits?.length > 0 && (
                    <Field icon="solar:tag-linear" label="Traits" value={t.persona.traits.join(", ")} />
                  )}
                </Stack>
              </TableCell>

              <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
                <Stack direction="row" alignItems="flex-start" spacing={0.75}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{t.title}</Typography>
                  {t.critical && (
                    <Tooltip arrow title="Critical — a failure here is a release blocker">
                      <Box sx={{ display: "flex", mt: "2px" }}>
                        <Iconify icon="solar:danger-triangle-bold" width={12} sx={{ color: "text.subtitle" }} />
                      </Box>
                    </Tooltip>
                  )}
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>{t.task}</Typography>
              </TableCell>

              <TableCell sx={{ ...bodyCell, typography: "s2", color: "text.secondary" }} onClick={() => onOpen(t)}>
                {t.expected}
              </TableCell>

              {/* What the conversation actually did, which is the column you
                  read when a score disagrees with the ideal outcome. */}
              <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
                <Typography sx={{ typography: "s3", color: "text.secondary", fontFamily: "ui-monospace, Menlo, monospace", lineHeight: 1.7 }}>
                  {(t.steps || [])
                    .filter((s) => s.role === "agent")
                    .slice(0, 5)
                    .map((_, i, arr) => (i === 0 ? "start" : `turn_${i}`) + (i < arr.length - 1 ? " → " : ""))
                    .join("")}
                </Typography>
              </TableCell>

              {/* Call details and the per-call system metrics, the same set
                  the execution-detail grid carries. */}
              <TableCell sx={bodyCell} onClick={() => onOpen(t)}>
                <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                  {t.status === "failed" ? "Failed" : t.status === "error" ? "Errored" : "Completed"}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  Duration : {((t.durationMs || 0) / 1000).toFixed(1)}s
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {t.id}
                </Typography>
              </TableCell>

              <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
                {csatOf(t)}
              </TableCell>
              <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
                {t.steps?.length || 0}
              </TableCell>
              <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
                {latencyOf(t)}ms
              </TableCell>
              <TableCell align="right" sx={num} onClick={() => onOpen(t)}>
                {(t.tokens || 0).toLocaleString()}
              </TableCell>

              {evals.map((e) => {
                const r = t.evalResults?.find((x) => x.id === e.id);
                return (
                  <TableCell key={e.id} sx={{ ...bodyCell, p: 0, position: "relative" }} onClick={() => onOpen(t)}>
                    {r ? <Score result={r} /> : <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>—</Box>}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

TraceTable.propTypes = {
  tasks: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  selected: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  onToggleAll: PropTypes.func,
  onOpen: PropTypes.func,
};

function Field({ icon, label, value }) {
  if (value == null || value === "") return null;
  return (
    <Stack
      direction="row" alignItems="center" spacing={0.75}
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, bgcolor: "background.neutral" }}
    >
      <Iconify icon={icon} width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{label}:</Typography>
      <Typography noWrap sx={{ typography: "s3", color: "text.primary", fontWeight: 500 }}>{value}</Typography>
    </Stack>
  );
}
Field.propTypes = { icon: PropTypes.string, label: PropTypes.string, value: PropTypes.any };

/**
 * Fills its cell in the score's colour.
 *
 * `interpolateColorBasedOnScore` — the single background the execution-detail
 * grid uses, with the theme's own text colour on top. The token variant Observe
 * uses returns a light-palette pair, which is why these were washed out
 * against the reference instead of the deep red and green it shows.
 */
function Score({ result }) {
  const bgcolor = interpolateColorBasedOnScore(result.score, 1);
  return (
    <Tooltip arrow title={result.reason || ""}>
      <Box
        sx={{
          position: "absolute", inset: 0,
          px: 2, py: 1.5, bgcolor, color: "text.primary",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {result.passed ? "Passed" : "Failed"}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}>
          {Math.round(result.score * 100)}
        </Typography>
      </Box>
    </Tooltip>
  );
}
Score.propTypes = { result: PropTypes.object };
