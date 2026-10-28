import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SoonChip from "../../components/SoonChip";
import { RunLetter } from "./RunPills";
import { PAIR_FLOOR } from "./pairedInterval";

const signed = (v) =>
  v == null
    ? "-"
    : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(1)}`;

const LABEL_ICON = {
  improved: "solar:arrow-up-linear",
  regressed: "solar:arrow-down-linear",
  same: "solar:minus-circle-linear",
};

const ROW_SX = { px: 2, borderTop: "1px solid", borderColor: "divider" };

// "env v2 · scenarios v3 · agent v4": the versions a run was pinned to.
const pinsOf = (run) =>
  [
    run?.environmentVersion?.label && `env ${run.environmentVersion.label}`,
    run?.suiteVersion?.label && `scenarios ${run.suiteVersion.label}`,
    run?.pinnedAgentVersion?.label && `agent ${run.pinnedAgentVersion.label}`,
  ]
    .filter(Boolean)
    .join(" · ");

/**
 * The verdict the comparison earns: the baseline against the newest run,
 * over the scenarios both measured, with its 95% interval. Red is for a
 * regression only. Each run's pinned agent, environment and scenario versions
 * show when the runs carry them; a measured noise band and the evaluations
 * and caller each run held fixed aren't in the API yet.
 */
export default function CompareVerdict({ read, runs = [] }) {
  if (!read) return null;
  const { interval: iv, label, trend } = read;
  const floor = label.id === "rows";
  const bad = label.id === "regressed";
  const runOf = (id) => runs.find((r) => r.id === id);
  const pinned = runs.some((r) => pinsOf(r));

  return (
    <Box
      data-testid="compare-verdict"
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Box sx={{ px: 2, py: 1.5 }}>
        {floor ? (
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            <Box
              component="span"
              sx={{ fontWeight: "fontWeightSemiBold", color: "text.primary" }}
            >
              {`Only ${iv.n} comparable pair${iv.n === 1 ? "" : "s"}.`}
            </Box>
            {` Below ${PAIR_FLOOR} there's no aggregate. Read the rows below, regressions first.`}
          </Typography>
        ) : (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{ flexWrap: "wrap", rowGap: 0.75 }}
          >
            <CustomTooltip
              show
              arrow
              size="small"
              title={`${label.note} Improved or Regressed when the 95% interval excludes zero; No meaningful change when it sits inside ±10 points; Inconclusive otherwise.`}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{
                  px: 0.875,
                  py: 0.25,
                  borderRadius: "4px",
                  border: "1px solid",
                  borderColor: bad ? "error.main" : "divider",
                  bgcolor: (t) =>
                    bad ? alpha(t.palette.error.main, 0.08) : "transparent",
                }}
              >
                <Iconify
                  icon={LABEL_ICON[label.id] || "solar:question-circle-linear"}
                  width={14}
                  sx={{ color: bad ? "error.main" : "text.secondary" }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: "fontWeightSemiBold",
                    color: bad ? "error.main" : "text.primary",
                  }}
                >
                  {label.label}
                </Typography>
              </Stack>
            </CustomTooltip>
            <Typography
              sx={{
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
                fontVariantNumeric: "tabular-nums",
                color: bad ? "error.main" : "text.primary",
              }}
            >
              {`${signed(iv.delta)} pts`}
            </Typography>
            <CustomTooltip
              show
              arrow
              size="small"
              title={`95% interval ${signed(iv.lo)} to ${signed(iv.hi)} pts over ${iv.n} measured pair${iv.n === 1 ? "" : "s"}. ${read.sentence}.`}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                  borderBottom: "1px dotted",
                  borderColor: "text.disabled",
                }}
              >
                {`pass rate, on ${iv.n} of ${read.total} scenarios`}
              </Typography>
            </CustomTooltip>
            <SoonChip
              label="Noise band" detail="How much this agent's pass rate moves between identical runs, measured, so the verdict can say whether a change is real."
              what="A noise band measured for this agent"
              size="tiny"
            />
          </Stack>
        )}
      </Box>

      {trend && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ ...ROW_SX, py: 1, flexWrap: "wrap", rowGap: 0.5 }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.subtitle",
            }}
          >
            Trend
          </Typography>
          {trend.n > 0 &&
            trend.points.map((p, i) => {
              const run = runOf(p.runId);
              return (
                <Stack
                  key={p.runId}
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                >
                  {i > 0 && (
                    <Iconify
                      icon="solar:arrow-right-linear"
                      width={12}
                      sx={{ color: "text.disabled" }}
                    />
                  )}
                  {run && <RunLetter letter={run.letter} color={run.color} />}
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: "fontWeightSemiBold",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {`${p.rate}%`}
                  </Typography>
                </Stack>
              );
            })}
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {trend.n
              ? `on the ${trend.n} scenario${trend.n === 1 ? "" : "s"} every run measured`
              : `no scenario was measured in all ${trend.points.length} runs`}
          </Typography>
        </Stack>
      )}

      <Stack
        direction="row"
        alignItems="center"
        useFlexGap
        spacing={1.25}
        sx={{ ...ROW_SX, py: 0.875, flexWrap: "wrap", rowGap: 0.5 }}
      >
        <Typography
          sx={{
            typography: "s3",
            fontWeight: "fontWeightSemiBold",
            color: "text.secondary",
          }}
        >
          What each run pinned
        </Typography>
        {pinned ? (
          <>
            {runs.map((r) => (
              <Stack
                key={r.id}
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{ minWidth: 0 }}
              >
                <RunLetter letter={r.letter} color={r.color} />
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.secondary" }}
                >
                  {pinsOf(r) || "-"}
                </Typography>
              </Stack>
            ))}
            <SoonChip
              label="Evaluations and caller" detail="The evaluations and simulated caller each run held fixed, so you know the comparison is like for like."
              what="The evaluations and simulated caller each run held fixed"
              size="tiny"
            />
          </>
        ) : (
          <SoonChip
            label="Held fixed" detail="What each run pinned (environment version, evaluations, simulated caller), so you know the comparison is like for like."
            what="What each run held fixed (environment version, evaluations, simulated caller)"
            size="tiny"
          />
        )}
      </Stack>
    </Box>
  );
}
CompareVerdict.propTypes = {
  read: PropTypes.object,
  runs: PropTypes.array,
};
