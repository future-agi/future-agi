import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Stack, Switch, Typography } from "@mui/material";
import {
  SAMPLE_REPLAY_HEADER,
  SAMPLE_REPLAY_SDK,
  sampleSideEffectTools,
} from "src/api/simulate-environments/_fixtures/detail/settings";
import CopyField from "../../components/CopyField";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import SettingsCard from "./SettingsCard";
import { DIVIDER_SX, KEY_W, MONO } from "./settings.constants";

const LABEL_SX = { typography: "s3", color: "text.subtitle", mb: 0.5 };

// The flag every simulated call would carry, and which tools the agent should
// hold back when it sees it. The tools are the environment's real ones; the
// flag itself and the side-effect marks have no API yet, so a mark saves
// nothing and none are guessed on a real environment.
export default function ReplayFlagCard({
  tools,
  disabled = false,
  sample = false,
}) {
  const [marked, setMarked] = useState(() =>
    sample ? sampleSideEffectTools(tools) : [],
  );
  const toggle = (name, on) => {
    setMarked((m) => (on ? [...m, name] : m.filter((n) => n !== name)));
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };
  return (
    <SettingsCard
      label="Replay flag"
      title="Replay flag"
      subtitle="Every simulated call says it is one, so your agent can hold back tools with real side effects"
      action={<UiOnlyFlag feature="environmentSettings" />}
    >
      <Box sx={{ p: 2.5 }}>
        {sample ? (
          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            <Box sx={{ flex: 1, minWidth: 0, maxWidth: 360 }}>
              <Typography sx={LABEL_SX}>HTTP header</Typography>
              <CopyField value={SAMPLE_REPLAY_HEADER} />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0, maxWidth: 360 }}>
              <Typography sx={LABEL_SX}>SDK context</Typography>
              <CopyField value={SAMPLE_REPLAY_SDK} />
            </Box>
          </Stack>
        ) : (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              HTTP header and SDK context
            </Typography>
            <SoonChip
              label="Replay flag"
              what="The replay flag on simulated calls"
            />
          </Stack>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={0.75}
        sx={{
          px: 2.5,
          py: 1,
          borderTop: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Tools
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`${marked.length} of ${tools.length} marked as side effect`}
        </Typography>
      </Stack>
      {tools.length === 0 ? (
        <Typography
          sx={{ px: 2.5, py: 1.5, typography: "s2", color: "text.subtitle" }}
        >
          This environment has no tools yet, so there is nothing to hold back.
        </Typography>
      ) : (
        <Stack divider={<Box sx={DIVIDER_SX} />}>
          {tools.map((t) => {
            const on = marked.includes(t.name);
            return (
              <Stack
                key={t.name}
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{ px: 2.5, py: 1 }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    fontFamily: MONO,
                    width: KEY_W,
                    flexShrink: 0,
                  }}
                >
                  {t.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    typography: "s2",
                    color: "text.subtitle",
                  }}
                >
                  {t.desc && t.desc !== t.name ? t.desc : ""}
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ flexShrink: 0 }}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      color: on ? "text.primary" : "text.subtitle",
                    }}
                  >
                    Side effect
                  </Typography>
                  <Switch
                    size="small"
                    checked={on}
                    disabled={disabled}
                    onChange={(e) => toggle(t.name, e.target.checked)}
                    inputProps={{ "aria-label": `${t.name} has a side effect` }}
                  />
                </Stack>
              </Stack>
            );
          })}
        </Stack>
      )}
      <Typography
        sx={{
          px: 2.5,
          py: 1.5,
          typography: "s3",
          color: "text.subtitle",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        Marked tools run against the environment&apos;s sandboxed version. Your
        agent should skip the real call when it sees the flag.
      </Typography>
    </SettingsCard>
  );
}
ReplayFlagCard.propTypes = {
  tools: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, desc: PropTypes.string }),
  ).isRequired,
  disabled: PropTypes.bool,
  sample: PropTypes.bool,
};
