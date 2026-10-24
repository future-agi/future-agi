import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { summaryRows } from "../_mock/compareView";
import { runIdentityOf } from "../_mock/runIdentity";
import { textTone } from "../components/textTone";
import { pairCountsVsBaseline, PAIR_CLASSES } from "../_mock/pairs";

/* "54 compared · 4 passed → failed · 2 failed → passed · 3 new" — each run against the baseline. */
const pairLine = (read) => {
  if (!read) return "—";
  const c = read.counts;
  const outside = ["changed", "new", "not-applicable", "archived", "removed"]
    .filter((k) => c[k])
    .map((k) => `${c[k]} ${PAIR_CLASSES[k].short.toLowerCase()}`);
  return [
    `${c.exact + c.regraded + c.rechecked} compared`,
    read.regressed && `${read.regressed} passed → failed`,
    read.improved && `${read.improved} failed → passed`,
    ...outside,
  ]
    .filter(Boolean)
    .join(" · ");
};

/**
 * The comparison with the scenarios taken out.
 *
 * Sometimes the question is not "which scenario moved" but "is this version
 * better", and that is answered by a dozen numbers rather than by thirty rows.
 * Every figure here also exists on the runs table; what this adds is that only
 * the compared runs are present, and every one of them is read against the
 * baseline.
 */
export default function CompareSummary({ comparison, evals, env, envState }) {
  const rows = summaryRows(comparison, evals);
  const { runs, baseline } = comparison;

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 220 + runs.length * 180 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ px: 2.5, py: 1.5 }} />
          {runs.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ px: 2, py: 1.5 }}
            >
              <Box
                sx={{
                  width: 20,
                  height: 20,
                  borderRadius: "4px",
                  flexShrink: 0,
                  display: "grid",
                  placeItems: "center",
                  typography: "s3",
                  fontWeight: 600,
                  color: textTone(r.color),
                  bgcolor: (t) =>
                    alpha(r.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                }}
              >
                {r.letter}
              </Box>
              <Box minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {runIdentityOf(r, envState, env).label}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {r.id === baseline.id
                    ? "baseline"
                    : r.kind === "trial"
                      ? `Run ${r.trialN} · ${r.selfImprovementName}`
                      : `Run ${r.ordinal ?? (r.index != null ? r.index + 1 : "")}`}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Box>

        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {/* Each run against the baseline, pair by pair: how many compared,
              the flips among them, and what sat outside the delta. */}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
              alignItems: "center",
            }}
          >
            <Typography
              sx={{
                px: 2.5,
                py: 1.5,
                typography: "s2",
                color: "text.secondary",
              }}
            >
              Pairs against the baseline
            </Typography>
            {runs.map((r, i) => {
              const read = i
                ? pairCountsVsBaseline(comparison, i, env, envState)
                : null;
              return (
                <Box key={r.id} sx={{ px: 2, py: 1.5 }}>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                      color: i ? "text.primary" : "text.tertiary",
                    }}
                  >
                    {i ? pairLine(read) : "—"}
                  </Typography>
                  {/* Across environment versions there is no delta — side by side only. */}
                  {read && read.label.id === "side" && (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      Side by side · no verdict across environment versions
                    </Typography>
                  )}
                  {read &&
                    !["rows", "side"].includes(read.label.id) &&
                    read.interval.delta != null && (
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {`${read.interval.delta > 0 ? "+" : ""}${read.interval.delta} pts · ${read.interval.lo} to ${read.interval.hi} · ${read.label.label}`}
                      </Typography>
                    )}
                </Box>
              );
            })}
          </Box>
          {rows.map((line) => (
            <Box
              key={line.id}
              sx={{
                display: "grid",
                gridTemplateColumns: `220px repeat(${runs.length}, 1fr)`,
                alignItems: "center",
              }}
            >
              <Typography
                sx={{
                  px: 2.5,
                  py: 1.5,
                  typography: "s2",
                  color: "text.secondary",
                }}
              >
                {line.label}
              </Typography>
              {line.values.map((v) => (
                <Stack
                  key={v.run.id}
                  direction="row"
                  alignItems="baseline"
                  spacing={0.75}
                  sx={{ px: 2, py: 1.5 }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {v.text}
                  </Typography>
                  {v.moved && (
                    <Stack direction="row" alignItems="center" spacing={0.125}>
                      <Iconify
                        icon={
                          v.better
                            ? "solar:arrow-up-linear"
                            : "solar:arrow-down-linear"
                        }
                        width={12}
                        sx={{
                          color: v.better
                            ? "status.success.fg"
                            : "status.danger.fg",
                        }}
                      />
                      <Typography
                        sx={{
                          typography: "s3",
                          fontWeight: 600,
                          color: v.better
                            ? "status.success.fg"
                            : "status.danger.fg",
                        }}
                      >
                        {v.better ? "better" : "worse"}
                      </Typography>
                    </Stack>
                  )}
                </Stack>
              ))}
            </Box>
          ))}
        </Stack>
      </Box>
    </Box>
  );
}

CompareSummary.propTypes = {
  comparison: PropTypes.object,
  evals: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
};
