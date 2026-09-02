import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, TextField, MenuItem, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../../components/primitives";
import { AXES, buildMatrix, coverageGaps, ruleCoverage } from "../../_mock/coverage";

/**
 * Coverage.
 *
 * A scenario count is not coverage. This cross-tabulates the suite so an empty
 * cell is the finding — "nothing here tests an adversarial user on the hard
 * path" is the sentence worth putting in front of someone, and a total of 32
 * never says it.
 */
export default function CoverageMatrix({ scenarios, env }) {
  const [rowAxis, setRowAxis] = useState("mode");
  const [colAxis, setColAxis] = useState("difficulty");
  const m = buildMatrix(scenarios, rowAxis, colAxis);
  const gaps = coverageGaps(scenarios);
  /* The gap that costs a guardrail rather than some variety. */
  const rules = ruleCoverage(env, scenarios);
  const uncovered = rules.filter((r) => r.count === 0);

  return (
    <SectionCard
      title="Coverage"
      subtitle={`${m.total} scenarios across ${m.rowKeys.length} × ${m.colKeys.length} — empty cells are the gaps`}
      action={
        <Stack direction="row" spacing={1}>
          <AxisPick label="Rows" value={rowAxis} onChange={setRowAxis} exclude={colAxis} />
          <AxisPick label="Columns" value={colAxis} onChange={setColAxis} exclude={rowAxis} />
        </Stack>
      }
    >
      {/*
        Guardrail coverage, above the axes.

        The matrix measures variety — modes, difficulties, personas. This
        measures the thing the environment exists for: every hard rule, and
        whether anything actually tests it. A suite can be beautifully varied
        and still leave the rule that stops a refund going out untested.
      */}
      <Box sx={{ px: 2.5, pt: 2, pb: 0.5 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Guardrails
          </Typography>
          <Typography sx={{ typography: "s3", color: uncovered.length ? "#DC2626" : "#16A34A", fontWeight: 700 }}>
            {rules.length - uncovered.length}/{rules.length} tested
          </Typography>
        </Stack>
        <Stack spacing={0.5}>
          {rules.map((r) => (
            <Stack key={r.rule} direction="row" alignItems="flex-start" spacing={1}>
              <Iconify
                icon={r.count ? "solar:check-circle-bold" : "solar:close-circle-bold"}
                width={13}
                sx={{ color: r.count ? "#16A34A" : "#DC2626", flexShrink: 0, mt: "2px" }}
              />
              <Typography sx={{ typography: "s2", color: r.count ? "text.secondary" : "text.primary", flex: 1, minWidth: 0 }}>
                {r.rule}
              </Typography>
              <Typography noWrap sx={{ typography: "s3", color: r.count ? "text.subtitle" : "#DC2626", flexShrink: 0 }}>
                {r.count
                  ? `${r.count} scenario${r.count === 1 ? "" : "s"}${r.critical ? " · blocker" : ""}`
                  : "nothing tests this"}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Box sx={{ p: 2.5, overflowX: "auto" }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `minmax(140px, max-content) repeat(${m.colKeys.length}, minmax(84px, 1fr))`,
            gap: 0.5, minWidth: "max-content",
          }}
        >
          <Box />
          {m.colKeys.map((c) => (
            <Typography
              key={c}
              sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textAlign: "center", pb: 0.5, textTransform: "uppercase", letterSpacing: .3 }}
            >
              {m.colAxis.labelOf(c)}
            </Typography>
          ))}

          {m.rowKeys.map((r) => (
            <Box key={r} sx={{ display: "contents" }}>
              <Typography sx={{ typography: "s2", fontWeight: 600, alignSelf: "center", pr: 1.5 }}>
                {m.rowAxis.labelOf(r)}
              </Typography>
              {m.colKeys.map((c) => {
                const n = m.at(r, c);
                /*
                  Two-band heatmap: covered cells sit on a green ramp (from a
                  soft tint at 1 scenario to a saturated fill at m.max), empty
                  cells sit on a red tint with a dashed border. The earlier
                  version used the brand purple at 0.08–0.42 opacity, which
                  compressed all the covered cells into a single muted band —
                  a 6 looked no different from a 2, and the whole matrix read
                  as grey. Green also gives "empty = red = gap" a natural
                  opposite instead of "empty = red, filled = brand purple",
                  which felt like two unrelated colours.
                */
                const t = m.max > 1 ? (n - 1) / (m.max - 1) : (n > 0 ? 1 : 0);
                /*
                  Tighter, lighter opacity band — earlier 0.14→0.64 read
                  as saturated on dark, and the max fills punched. This
                  gives a soft floor, a soft ceiling, still with visible
                  gradient between "2" and "6".
                */
                const opacity = n ? 0.08 + 0.22 * t : 0;
                const strong = n && n / m.max >= 0.85;
                return (
                  <Tooltip
                    key={c} arrow
                    title={n ? `${n} scenario${n === 1 ? "" : "s"}` : "No scenario covers this — worth generating some"}
                  >
                    <Box
                      sx={{
                        height: 44, borderRadius: 1, display: "grid", placeItems: "center",
                        border: "1px solid",
                        borderColor: n
                          ? alpha("#16A34A", 0.12 + 0.2 * t)
                          : alpha("#DC2626", 0.28),
                        borderStyle: n ? "solid" : "dashed",
                        bgcolor: n
                          ? alpha("#16A34A", opacity)
                          : (th) => alpha("#DC2626", th.palette.mode === "dark" ? 0.05 : 0.035),
                        transition: "background-color 0.15s ease",
                      }}
                    >
                      <Typography
                        sx={{
                          typography: "s1", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                          color: n
                            ? strong
                              ? "#fff"
                              : "text.primary"
                            : alpha("#DC2626", 0.85),
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

        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1.75 }}>
          Difficulty is derived, not assigned: a scenario that probes a rule, or runs ten turns
          or more, counts as hard.
        </Typography>
      </Box>

      {gaps.length > 0 && (
        <Box
          sx={{
            px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider",
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.07 : 0.035),
          }}
        >
          <Stack direction="row" spacing={1.25} alignItems="flex-start">
            <Iconify icon="solar:danger-triangle-bold" width={16} sx={{ color: "#DC2626", flexShrink: 0, mt: "1px" }} />
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.5 }}>
                {gaps.length} uncovered {gaps.length === 1 ? "combination" : "combinations"}
              </Typography>
              <Stack spacing={0.375}>
                {gaps.slice(0, 4).map((g) => (
                  <Typography key={g.id} sx={{ typography: "s2", color: "text.secondary" }}>
                    <b>{g.label}</b> — {g.blurb}
                  </Typography>
                ))}
                {gaps.length > 4 && (
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    and {gaps.length - 4} more
                  </Typography>
                )}
              </Stack>
            </Box>
          </Stack>
        </Box>
      )}
    </SectionCard>
  );
}

CoverageMatrix.propTypes = { env: PropTypes.object, scenarios: PropTypes.array.isRequired };

function AxisPick({ label, value, onChange, exclude }) {
  return (
    <TextField
      select size="small" label={label} value={value}
      onChange={(e) => onChange(e.target.value)}
      sx={{ minWidth: 130, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
    >
      {AXES.filter((a) => a.id !== exclude).map((a) => (
        <MenuItem key={a.id} value={a.id} sx={{ typography: "s2" }}>{a.label}</MenuItem>
      ))}
    </TextField>
  );
}
AxisPick.propTypes = { label: PropTypes.string, value: PropTypes.string, onChange: PropTypes.func, exclude: PropTypes.string };
