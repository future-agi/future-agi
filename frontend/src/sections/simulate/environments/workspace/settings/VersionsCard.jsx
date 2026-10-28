import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Chip, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SAMPLE_ENV_VERSIONS } from "src/api/simulate-environments/_fixtures/detail/versions";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import SettingsCard from "./SettingsCard";
import { DIVIDER_SX, MONO } from "./settings.constants";

// Environment versions aren't in the API: a real environment shows a soon
// chip and no rows. With ?uiSample the card shows Vel's history, flagged, and
// Roll back saves nothing.
export default function VersionsCard({ disabled = false, sample = false }) {
  return (
    <SettingsCard
      label="Versions"
      title="Versions"
      subtitle="A version covers the world, scenarios, evaluations, caller and credentials. Every run pins one."
      action={
        sample ? (
          <UiOnlyFlag feature="environmentVersions" />
        ) : (
          <SoonChip label="Versions" what="Environment versions" plural />
        )
      }
    >
      {sample && (
        <Stack divider={<Box sx={DIVIDER_SX} />}>
          {SAMPLE_ENV_VERSIONS.map((v) => (
            <Stack
              key={v.label}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2.5, py: 1 }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  fontFamily: MONO,
                  width: 44,
                  flexShrink: 0,
                }}
              >
                {v.label}
              </Typography>
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                    {v.summary}
                  </Typography>
                  {v.latest && (
                    <Chip
                      size="small"
                      label="Current"
                      sx={{ color: "success.dark", bgcolor: "success.lighter" }}
                    />
                  )}
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {[
                    v.ago,
                    v.kinds,
                    `${v.runs} run${v.runs === 1 ? "" : "s"}`,
                  ].join(" · ")}
                </Typography>
              </Box>
              {!v.latest && (
                <Button
                  size="small"
                  disabled={disabled}
                  onClick={() =>
                    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" })
                  }
                  startIcon={<Iconify icon="solar:history-linear" width={14} />}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                >
                  Roll back
                </Button>
              )}
            </Stack>
          ))}
        </Stack>
      )}
    </SettingsCard>
  );
}
VersionsCard.propTypes = { disabled: PropTypes.bool, sample: PropTypes.bool };
