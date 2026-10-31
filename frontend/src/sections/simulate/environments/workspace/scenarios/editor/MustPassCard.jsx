import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Stack, Switch, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { EDITOR_COPY } from "../scenarioEditor.constants";

// The must-pass mark: on the set, not the scenario, so it never re-proves it.
// The API has no must-pass flag yet, so the switch only moves here.
export default function MustPassCard({ checked, onChange }) {
  return (
    <Stack
      component="section"
      aria-label={EDITOR_COPY.mustPass}
      direction="row"
      alignItems="flex-start"
      spacing={1.25}
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon={checked ? "solar:shield-check-bold" : "solar:shield-check-linear"}
        width={16}
        sx={{
          color: checked ? "text.primary" : "text.disabled",
          mt: "2px",
          flexShrink: 0,
        }}
      />
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography
            sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
          >
            {EDITOR_COPY.mustPass}
          </Typography>
          <UiOnlyFlag feature="mustPass" />
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {EDITOR_COPY.mustPassHint}
        </Typography>
      </Box>
      <Switch
        size="small"
        checked={checked}
        onChange={(e) => {
          onChange(e.target.checked);
          enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
        }}
        inputProps={{ "aria-label": EDITOR_COPY.mustPass }}
        sx={{ flexShrink: 0, mt: "-2px" }}
      />
    </Stack>
  );
}
MustPassCard.propTypes = {
  checked: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
