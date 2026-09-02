import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Scenarios down, trials across.
 *
 * The first version of this had it the other way round, and the orientation was
 * the whole problem: scenario names are sentences and trial ids are single
 * digits, so putting scenarios across the top meant rotating five long labels
 * to sixty degrees, truncating them to "Do not rebook without…", and stacking
 * twelve rows of tiny squares down the left of a panel a thousand pixels wide.
 * Transposed, every name is horizontal and readable, the trials are the narrow
 * axis they should always have been, and the width goes to the data.
 *
 * Two things the ranked trials table cannot show, and the reason this view
 * exists at all:
 *
 *   Which scenarios are stubborn. A row that is grey the whole way across is a
 *   scenario no candidate fixed — that is not a search result, it is a scenario
 *   that needs a different kind of change, and it is invisible in a leaderboard.
 *
 *   Which candidates are complementary. Two trials a point apart usually fix
 *   different things, and the combination beats either. That is the premise of
 *   a Pareto search, and it is stated at the bottom rather than left for someone
 *   to spot by eye.
 */

const CELL = {
  fixed: { bg: "#16A34A", op: 0.8, label: "fixed" },
  broke: { bg: "#DC2626", op: 0.8, label: "broke" },
  "still-failing": { bg: "#94A3B8", op: 0.25, label: "still failing" },
  same: { bg: null, op: 0, label: "unchanged" },
};

const NAME_W = 330;
const SUM_W = 150;
/* Wider when there are few trials, so a four-trial search does not draw four
   postage stamps across a thousand-pixel panel. */
const colWidth = (n) => (n <= 6 ? 46 : n <= 12 ? 36 : 30);

export default function TrialHeatmap({ trials = [], scenarios = [], winner }) {
  const COL = colWidth(trials.length);
  /*
    Per scenario: how many candidates moved it, and which way. Sorted so the
    rows that need a decision are at the top — blockers first, then whatever
    the fewest trials could fix.
  */
  const rows = useMemo(() => {
    const list = scenarios.map((s) => {
      const fixed = trials.filter((t) => t.perScenario?.[s.id] === "fixed").length;
      const broke = trials.filter((t) => t.perScenario?.[s.id] === "broke").length;
      /*
        Only a scenario that was failing can be "never fixed". A passing one
        showed 0/12 in amber, which read as a problem the search had failed at
        when in fact there was nothing there to fix — the only thing that row
        can report is whether a candidate broke it.
      */
      const wasFailing = trials.some((t) => ["fixed", "still-failing"].includes(t.perScenario?.[s.id]));
      return { ...s, fixed, broke, wasFailing };
    });
    return list.sort(
      (a, b) => (b.critical ? 1 : 0) - (a.critical ? 1 : 0)
        || (a.wasFailing ? 0 : 1) - (b.wasFailing ? 0 : 1)
        || a.fixed - b.fixed,
    );
  }, [scenarios, trials]);

  /* The best pair whose fixes do not overlap — the combination worth trying. */
  const pair = useMemo(() => {
    const fixesOf = (t) => new Set(scenarios.filter((s) => t.perScenario?.[s.id] === "fixed").map((s) => s.id));
    let best = null;
    trials.forEach((a, i) => {
      trials.slice(i + 1).forEach((b) => {
        if (a.brokeBlocker || b.brokeBlocker) return;
        const fa = fixesOf(a);
        const fb = fixesOf(b);
        const union = new Set([...fa, ...fb]).size;
        if (union > Math.max(fa.size, fb.size) && (!best || union > best.union)) {
          best = { a, b, union, alone: Math.max(fa.size, fb.size) };
        }
      });
    });
    return best;
  }, [trials, scenarios]);

  if (!trials.length || !scenarios.length) return null;

  return (
    <Box sx={{ p: 2.5 }}>
      <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 2 }} flexWrap="wrap" rowGap={1}>
        <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1, minWidth: 260 }}>
          Every training scenario against every trial. A row that stays grey is a scenario no candidate could
          fix — a different kind of change, not a better prompt.
        </Typography>
        <Stack direction="row" spacing={1.25} flexWrap="wrap" rowGap={0.5} sx={{ flexShrink: 0 }}>
          {["fixed", "broke", "still-failing", "same"].map((k) => (
            <Stack key={k} direction="row" alignItems="center" spacing={0.5}>
              <Box
                sx={{
                  width: 10, height: 10, borderRadius: "3px", flexShrink: 0,
                  border: CELL[k].bg ? "none" : "1px solid",
                  borderColor: "divider",
                  bgcolor: CELL[k].bg ? alpha(CELL[k].bg, CELL[k].op) : "transparent",
                }}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{CELL[k].label}</Typography>
            </Stack>
          ))}
        </Stack>
      </Stack>

      <Box sx={{ overflowX: "auto" }}>
        <Box sx={{ minWidth: NAME_W + trials.length * COL + SUM_W }}>
          {/* ── trial numbers ── */}
          <Stack direction="row" alignItems="flex-end" sx={{ mb: 0.75 }}>
            <Box sx={{ width: NAME_W, flexShrink: 0 }}>
              <Typography sx={{ typography: "s3", color: "text.disabled", fontWeight: 600 }}>
                Scenario
              </Typography>
            </Box>
            {trials.map((t) => {
              const win = winner && t.n === winner.n;
              return (
                <Tooltip
                  key={t.n} arrow
                  title={`Trial ${t.n} — ${t.score}% · ${t.tried}${t.brokeBlocker ? " · rejected, broke a blocker" : ""}`}
                >
                  <Box sx={{ width: COL, flexShrink: 0, textAlign: "center", cursor: "default" }}>
                    {t.brokeBlocker && (
                      <Iconify icon="solar:danger-triangle-bold" width={9} sx={{ color: "#DC2626", display: "block", mx: "auto" }} />
                    )}
                    <Typography
                      sx={{
                        typography: "s3", fontVariantNumeric: "tabular-nums",
                        color: win ? "#16A34A" : "text.disabled",
                        fontWeight: win ? 700 : 500,
                      }}
                    >
                      {t.n}
                    </Typography>
                  </Box>
                </Tooltip>
              );
            })}
            <Box sx={{ width: SUM_W, flexShrink: 0, pl: 1.5 }}>
              <Typography sx={{ typography: "s3", color: "text.disabled", fontWeight: 600 }}>
                Fixed by
              </Typography>
            </Box>
          </Stack>

          {/* ── one row per scenario ── */}
          <Stack>
            {rows.map((s) => {
              const stubborn = s.wasFailing && s.fixed === 0;
              return (
                <Stack
                  key={s.id} direction="row" alignItems="center"
                  sx={{
                    height: 34, borderRadius: 0.75,
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Stack
                    direction="row" alignItems="center" spacing={0.75}
                    sx={{ width: NAME_W, flexShrink: 0, pr: 1.5, minWidth: 0 }}
                  >
                    {s.critical && (
                      <Tooltip arrow title="Release blocker — a regression here stops a release">
                        <Box
                          sx={{ width: 5, height: 5, borderRadius: "50%", bgcolor: "#DC2626", flexShrink: 0 }}
                        />
                      </Tooltip>
                    )}
                    <Tooltip arrow title={s.title}>
                      <Typography
                        noWrap
                        sx={{
                          typography: "s2", flex: 1, minWidth: 0,
                          fontWeight: s.critical ? 700 : 500,
                          color: stubborn ? "text.subtitle" : "text.primary",
                        }}
                      >
                        {s.title}
                      </Typography>
                    </Tooltip>
                  </Stack>

                  {trials.map((t) => {
                    const state = t.perScenario?.[s.id] || "same";
                    const tone = CELL[state];
                    const win = winner && t.n === winner.n;
                    return (
                      <Box key={t.n} sx={{ width: COL, flexShrink: 0, px: "3px" }}>
                        <Tooltip arrow title={`Trial ${t.n} · ${s.title} — ${tone.label}`}>
                          <Box
                            sx={{
                              height: 24, borderRadius: "4px", cursor: "default",
                              border: tone.bg ? "none" : "1px solid",
                              borderColor: "divider",
                              bgcolor: tone.bg ? alpha(tone.bg, tone.op) : "transparent",
                              outline: win ? "1.5px solid" : "none",
                              outlineColor: alpha("#16A34A", 0.55),
                              outlineOffset: "1px",
                            }}
                          />
                        </Tooltip>
                      </Box>
                    );
                  })}

                  {/* How reachable this scenario is at all. */}
                  <Stack
                    direction="row" alignItems="center" spacing={1}
                    sx={{ width: SUM_W, flexShrink: 0, pl: 1.5 }}
                  >
                    {s.wasFailing ? (
                      <>
                        <Box sx={{ flex: 1, height: 4, borderRadius: 2, bgcolor: "divider", overflow: "hidden" }}>
                          <Box
                            sx={{
                              width: `${(s.fixed / trials.length) * 100}%`, height: "100%",
                              bgcolor: stubborn ? "transparent" : "#16A34A",
                            }}
                          />
                        </Box>
                        <Typography
                          sx={{
                            typography: "s3", fontVariantNumeric: "tabular-nums", flexShrink: 0,
                            color: stubborn ? "#CA8A04" : "text.secondary",
                            fontWeight: stubborn ? 700 : 500,
                          }}
                        >
                          {s.fixed}/{trials.length}
                        </Typography>
                      </>
                    ) : (
                      <Tooltip arrow title="This scenario already passed — the only thing a candidate can do here is break it">
                        <Typography sx={{ typography: "s3", color: "text.disabled", flex: 1 }}>
                          already passing
                        </Typography>
                      </Tooltip>
                    )}
                    {s.broke > 0 && (
                      <Tooltip arrow title={`${s.broke} ${s.broke === 1 ? "candidate" : "candidates"} broke this scenario`}>
                        <Typography sx={{ typography: "s3", color: "#DC2626", fontWeight: 700, flexShrink: 0 }}>
                          −{s.broke}
                        </Typography>
                      </Tooltip>
                    )}
                  </Stack>
                </Stack>
              );
            })}
          </Stack>
        </Box>
      </Box>

      {/* ── what the grid is actually saying ── */}
      <Stack spacing={1} sx={{ mt: 2 }}>
        {rows.some((s) => s.wasFailing && s.fixed === 0) && (
          <Note tone="#CA8A04" icon="solar:info-circle-bold">
            <Box component="span" sx={{ fontWeight: 700 }}>
              {rows.filter((s) => s.wasFailing && s.fixed === 0).length}{" "}
              {rows.filter((s) => s.wasFailing && s.fixed === 0).length === 1 ? "failing scenario was" : "failing scenarios were"}{" "}
              never fixed by any candidate.
            </Box>{" "}
            No wording gets there — these need a tool, a memory or an architecture change, which is what the
            hand-off changes are for.
          </Note>
        )}
        {pair && (
          <Note tone="#7857FC" icon="solar:layers-minimalistic-bold">
            <Box component="span" sx={{ fontWeight: 700 }}>
              Trials {pair.a.n} and {pair.b.n} fix different scenarios.
            </Box>{" "}
            Together they cover {pair.union} where the better of them alone covers {pair.alone} — worth running a
            candidate that carries both changes.
          </Note>
        )}
      </Stack>
    </Box>
  );
}

TrialHeatmap.propTypes = { trials: PropTypes.array, scenarios: PropTypes.array, winner: PropTypes.object };

function Note({ tone, icon, children }) {
  return (
    <Stack
      direction="row" alignItems="flex-start" spacing={1.25}
      sx={{
        px: 1.75, py: 1.25, borderRadius: 1, border: "1px solid",
        borderColor: alpha(tone, 0.3),
        bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.08 : 0.04),
      }}
    >
      <Iconify icon={icon} width={15} sx={{ color: tone, flexShrink: 0, mt: "2px" }} />
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{children}</Typography>
    </Stack>
  );
}

Note.propTypes = { tone: PropTypes.string, icon: PropTypes.string, children: PropTypes.node };
