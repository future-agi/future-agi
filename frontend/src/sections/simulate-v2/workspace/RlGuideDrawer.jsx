import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CodeBlock from "src/components/code-block";
import SideDrawer from "../components/SideDrawer";
import { RL_SCALE, rlLoopCodeOf } from "../_mock/release";

/*
  The full RL loop on this environment (T46: "docs show the full loop on one
  industry environment"; rl.md worked example). Train on the train split
  with many sessions at once, then score the test split through the same
  endpoint. We never see or change the trainer's model.
*/
export default function RlGuideDrawer({ open, onClose, env, envState }) {
  const locked = envState?.lockedRelease;
  const train = locked?.splits?.train?.length || 0;
  const test = locked?.splits?.test?.length || 0;
  const steps = [
    [
      "Pick the release and split",
      `Release ${locked?.version || "v1"} is locked: its ${train} train and ${test} test tasks and its scorer can't change, so rewards stay comparable.`,
    ],
    [
      "Create an API key",
      "One key per trainer. It opens sessions on this environment only.",
    ],
    [
      "Open many sessions at once",
      `Up to ${RL_SCALE.limit}. Each gets its own copy of the world with its seed data and simulated people, so two sessions changing the same record never see each other.`,
    ],
    [
      "Act and read the reward",
      "Your model calls the world's tools. Each result can carry a reward and finished; the final reward comes from the same checks that decide a run's pass or fail.",
    ],
    [
      "Score the test split",
      "Run the new checkpoint on the test split through the same endpoint. Answers and hidden checks stay with us.",
    ],
  ];
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
              Train on {env?.name || "this environment"}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              The full loop, from your trainer. Sessions use the same credits as
              simulations.
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}>
          <Stack spacing={1.5} sx={{ mb: 2.5 }}>
            {steps.map(([title, body], i) => (
              <Stack key={title} direction="row" spacing={1.5}>
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.tertiary",
                    width: 16,
                    flexShrink: 0,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {i + 1}
                </Typography>
                <Box minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    {title}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {body}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Stack>
          <CodeBlock code={rlLoopCodeOf(env, envState)} />
        </Box>
      </Box>
    </SideDrawer>
  );
}
RlGuideDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
};
