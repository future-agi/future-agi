import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SAMPLE_PRODUCTION_SCENARIOS_ON } from "src/api/simulate-environments/_fixtures/detail/settings";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import SettingsCard from "./SettingsCard";

const HELP_SX = {
  typography: "s3",
  color: "text.subtitle",
  mt: 0.25,
  maxWidth: 640,
};

// Whether Error Feed issues and production conversations may become
// scenarios. A workspace setting with no API yet: its state is a soon chip and
// the switch saves nothing.
export default function ProductionScenariosCard({
  disabled = false,
  sample = false,
}) {
  const on = sample ? SAMPLE_PRODUCTION_SCENARIOS_ON : false;
  return (
    <SettingsCard
      label="Scenarios from production"
      title="Scenarios from production"
      subtitle="A workspace setting. It applies to every environment."
      action={<UiOnlyFlag feature="environmentSettings" />}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={2}
        sx={{ p: 2.5 }}
      >
        <Box minWidth={0}>
          {sample ? (
            <>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Iconify
                  icon={
                    on
                      ? "solar:check-circle-linear"
                      : "solar:forbidden-circle-linear"
                  }
                  width={16}
                  sx={{ color: "text.subtitle", flexShrink: 0 }}
                />
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {on ? "On for this workspace" : "Off for this workspace"}
                </Typography>
              </Stack>
              <Typography sx={HELP_SX}>
                {on
                  ? "Error Feed and production conversations can become scenarios, with personal details replaced."
                  : "The Error Feed and production conversations are unavailable as sources on Add scenarios. Scenarios already added stay."}
              </Typography>
            </>
          ) : (
            <>
              <SoonChip
                label="Status"
                what="The workspace's production scenarios setting"
              />
              <Typography sx={{ ...HELP_SX, mt: 0.75 }}>
                When on, Error Feed issues and production conversations can
                become scenarios, with personal details replaced. Scenarios
                already added stay when it is off.
              </Typography>
            </>
          )}
        </Box>
        <Button
          size="small"
          variant="outlined"
          disabled={disabled}
          onClick={() => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" })}
          sx={{
            color: "text.secondary",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          {on ? "Turn off for workspace" : "Turn on for workspace"}
        </Button>
      </Stack>
    </SettingsCard>
  );
}
ProductionScenariosCard.propTypes = {
  disabled: PropTypes.bool,
  sample: PropTypes.bool,
};
