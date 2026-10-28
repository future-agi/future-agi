import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import SoonChip from "../../../../components/SoonChip";

const STATUS = {
  met: {
    icon: "solar:check-circle-linear",
    color: "status.success.fg",
    label: "Met",
  },
  failed: {
    icon: "solar:close-circle-linear",
    color: "status.danger.fg",
    label: "Failed",
  },
  inconclusive: {
    icon: "solar:question-circle-linear",
    color: "text.subtitle",
    label: "Inconclusive",
  },
};

const statusOf = (passed) =>
  passed === true ? "met" : passed === false ? "failed" : "inconclusive";

/**
 * Vel's Checklist: the scenario's sub-goals, each met, failed or not graded,
 * with the reason the sub-goal check gave. The reference path under it isn't
 * in the API yet.
 * @param {{ subGoalResults: Array<{ name: string, passed: ?boolean }>,
 *   checks: Array<{ name: string, reason: string }> }} props
 */
export default function CallChecklist({ subGoalResults = [], checks = [] }) {
  const met = subGoalResults.filter((g) => g.passed === true).length;
  const failed = subGoalResults.some((g) => g.passed === false);
  const reasonOf = (name) => checks.find((c) => c.name === name)?.reason;

  return (
    <Stack sx={{ p: 2 }} spacing={1}>
      {subGoalResults.length ? (
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                flex: 1,
                color: failed ? "status.danger.fg" : "text.primary",
              }}
            >
              {`${met} of ${subGoalResults.length} sub-goals met`}
            </Typography>
            <SoonChip
              label="Critical" detail="Critical sub-goals decide the verdict; supporting ones only add detail."
              what="Which sub-goals are critical"
              size="tiny"
            />
          </Stack>
          {subGoalResults.map((goal, i) => {
            const st = STATUS[statusOf(goal.passed)];
            const reason = reasonOf(goal.name);
            return (
              <Stack
                key={`${goal.name}-${i}`}
                direction="row"
                alignItems="flex-start"
                spacing={1}
                sx={{
                  px: 1.5,
                  py: 0.75,
                  "&:not(:last-of-type)": {
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  },
                }}
              >
                <Iconify
                  icon={st.icon}
                  width={14}
                  sx={{ color: st.color, flexShrink: 0, mt: "3px" }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {goal.name}
                  </Typography>
                  {reason && (
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {reason}
                    </Typography>
                  )}
                </Box>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    width: 76,
                    textAlign: "right",
                    flexShrink: 0,
                  }}
                >
                  {st.label}
                </Typography>
              </Stack>
            );
          })}
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          This scenario has no sub-goals.
        </Typography>
      )}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ pt: 1 }}>
        <Typography
          sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle" }}
        >
          Reference path
        </Typography>
        <SoonChip label="Reference path" detail="The path a known-good call takes through the scenario, to compare this call against." what="The reference path" />
      </Stack>
    </Stack>
  );
}
CallChecklist.propTypes = {
  subGoalResults: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, passed: PropTypes.bool }),
  ),
  checks: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, reason: PropTypes.string }),
  ),
};
