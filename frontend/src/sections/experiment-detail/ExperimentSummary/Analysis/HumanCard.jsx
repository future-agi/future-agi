import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Link, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";

const tabular = { fontVariantNumeric: "tabular-nums" };
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

const CountLink = ({ to, children }) =>
  to ? (
    <Link
      component={RouterLink}
      to={to}
      underline="hover"
      color="inherit"
      sx={{ fontWeight: 600 }}
    >
      {children}
    </Link>
  ) : (
    <Box component="span" sx={{ fontWeight: 600 }}>
      {children}
    </Box>
  );

CountLink.propTypes = { to: PropTypes.string, children: PropTypes.node };

/**
 * Human card (PRODUCT-SPEC §3.13 M8.3): pairwise preference for each
 * candidate (`#4A 24 · Tie 6 · #3A 10 · Win rate 68% (95% CI 52–80%) · Not
 * reviewed 8 → Review`), the read-only LLM judge line with its agreement,
 * and rubric label means. Counts open the grid on those rows.
 */
const HumanCard = ({ analysis, colors, links, bare = false }) => {
  const theme = useTheme();
  const runById = Object.fromEntries(analysis.runs.map((r) => [r.run_id, r]));
  const base = runById[analysis.baseline_run_id];
  const prefs = Object.entries(analysis.preference ?? {});
  const rubric = analysis.human ?? [];
  if (!prefs.length && !rubric.length) return null;

  return (
    <AnalysisCard
      id="metric-human"
      icon={bare ? undefined : "mdi:account-check-outline"}
      title={bare ? undefined : "Human review"}
      sx={bare ? { pt: 1.5 } : undefined}
    >
      <Stack gap={1.5}>
        {prefs.map(([runIdValue, p]) => {
          const cand = runById[runIdValue];
          const total = p.wins + p.ties + p.losses || 1;
          const segs = [
            {
              key: "wins",
              n: p.wins,
              label: cand.label,
              color: colors?.[cand.run_id]?.solid,
            },
            {
              key: "ties",
              n: p.ties,
              label: "Tie",
              color: theme.palette.grey[400],
            },
            {
              key: "losses",
              n: p.losses,
              label: base?.label,
              color: colors?.[base?.run_id]?.solid,
            },
          ];
          return (
            <Stack key={runIdValue} gap={0.75}>
              <Typography typography="s2" color="text.secondary">
                Overall preference · {cand.label} vs {base?.label} · {p.judged}{" "}
                of {p.items} rows judged
              </Typography>
              <Stack direction="row" sx={{ height: 12, gap: "2px" }}>
                {segs
                  .filter((s) => s.n > 0)
                  .map((s) => (
                    <CustomTooltip
                      key={s.key}
                      show
                      size="small"
                      placement="top"
                      arrow
                      title={`${s.label} preferred on ${s.n} rows`}
                    >
                      <Box
                        sx={{
                          width: `${(s.n / total) * 100}%`,
                          bgcolor: s.color,
                          borderRadius: "2px",
                        }}
                      />
                    </CustomTooltip>
                  ))}
              </Stack>
              <Typography typography="s2" sx={tabular}>
                {cand.label}{" "}
                <CountLink
                  to={links.pref(cand.run_id, cand.run_id, p.links?.wins)}
                >
                  {p.wins}
                </CountLink>
                {" · Tie "}
                <CountLink to={links.pref(cand.run_id, "tie", p.links?.ties)}>
                  {p.ties}
                </CountLink>
                {` · ${base?.label} `}
                <CountLink
                  to={links.pref(cand.run_id, base?.run_id, p.links?.losses)}
                >
                  {p.losses}
                </CountLink>
                {" · "}
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  arrow
                  title={`(wins + ties/2) / judged rows, Wilson 95% CI. ${p.judged} rows judged${p.low_sample ? " · Low sample: fewer than 30 rows" : ""}.`}
                >
                  <Box component="span" sx={{ fontWeight: 600 }}>
                    Win rate {pct(p.win_rate)}
                  </Box>
                </CustomTooltip>{" "}
                <Box component="span" sx={{ color: "text.secondary" }}>
                  (95% CI {pct(p.ci?.[0])}–{pct(p.ci?.[1])})
                </Box>
                {p.low_sample && (
                  <Box component="span" sx={{ color: "text.disabled" }}>
                    {" "}
                    n={p.judged}
                  </Box>
                )}
                {p.not_reviewed > 0 && (
                  <>
                    {" · Not reviewed "}
                    <Box component="span" sx={{ fontWeight: 600 }}>
                      {p.not_reviewed}
                    </Box>{" "}
                    <CustomTooltip
                      show
                      size="small"
                      placement="top"
                      arrow
                      title={`Open ${p.queue_name}`}
                    >
                      <Link
                        component={RouterLink}
                        to={links.review(p.queue_id)}
                        underline="hover"
                      >
                        Review
                      </Link>
                    </CustomTooltip>
                  </>
                )}
              </Typography>
              <Box
                sx={{
                  px: 1,
                  py: 0.75,
                  borderRadius: 0.5,
                  bgcolor: alpha(theme.palette.text.primary, 0.03),
                }}
              >
                <Typography typography="s2" color="text.secondary" sx={tabular}>
                  LLM judge ({p.judge.model}): {cand.label} {p.judge.wins} · Tie{" "}
                  {p.judge.ties} · {base?.label} {p.judge.losses} · agrees with
                  reviewers on {p.judge.agreement.agree} of{" "}
                  {p.judge.agreement.n} ({pct(p.judge.agreement.pct)})
                  {p.judge.disagree > 0 && (
                    <>
                      {" · "}
                      <Link
                        component={RouterLink}
                        to={links.disagree(cand.run_id)}
                        underline="hover"
                      >
                        {p.judge.disagree} disagreements
                      </Link>
                    </>
                  )}
                </Typography>
                <Typography typography="s3" color="text.disabled">
                  Read only. The judge never counts toward the win rate.
                </Typography>
              </Box>
            </Stack>
          );
        })}
        {rubric.length > 0 && (
          <Stack gap={0.5}>
            <Typography typography="s2" color="text.secondary">
              Rubric labels (mean of reviewers)
            </Typography>
            {rubric.map((h) => (
              <Stack
                key={`${h.run_id}-${h.key}`}
                direction="row"
                alignItems="center"
                gap={1}
              >
                <Box
                  sx={{
                    width: 3,
                    height: 14,
                    borderRadius: 1,
                    bgcolor: colors?.[h.run_id]?.solid,
                  }}
                />
                <Typography typography="s2" fontWeight={600} sx={{ width: 36 }}>
                  {runById[h.run_id]?.label}
                </Typography>
                <Typography typography="s2" sx={tabular}>
                  {h.name} {h.mean?.toFixed(1)}
                  {h.output_type === "stars" ? " / 5" : ""}
                </Typography>
                <Typography
                  typography="s3"
                  color={h.n < 30 ? "text.disabled" : "text.secondary"}
                >
                  n={h.n}
                </Typography>
              </Stack>
            ))}
          </Stack>
        )}
      </Stack>
    </AnalysisCard>
  );
};

HumanCard.propTypes = {
  analysis: PropTypes.object.isRequired,
  colors: PropTypes.object,
  links: PropTypes.object.isRequired,
  // one view: inside the `Human review` Section (its header is the title)
  bare: PropTypes.bool,
};

export default HumanCard;
