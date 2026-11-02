import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { EDIT_EFFECT } from "../scenarioEditor.constants";

// What the edit does to past results, read before the click that makes it
// true. It follows the saved changes only: a re-proving edit needs a re-run;
// a passes-when edit is a grader fix unless it now tests something else. The
// API has no scenario versions yet, so this is a preview only.
export default function VersionPreview({ rework }) {
  const [intent, setIntent] = useState("same");
  let effect = EDIT_EFFECT.minor;
  if (rework) effect = EDIT_EFFECT.major;
  else if (intent === "changed") effect = EDIT_EFFECT.retest;

  return (
    <Box
      component="section"
      aria-label="Version preview"
      sx={{
        mx: 2.5,
        mb: 1.5,
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        flexShrink: 0,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {effect.title}
        </Typography>
        <UiOnlyFlag feature="scenarioStates" />
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
        {effect.note}
      </Typography>
      {!rework && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Its checks changed.
          </Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={intent}
            onChange={(_, v) => v && setIntent(v)}
            sx={{
              "& .MuiToggleButton-root": {
                typography: "s3",
                textTransform: "none",
                py: 0.25,
              },
            }}
          >
            <ToggleButton value="same">Same intent (a fix)</ToggleButton>
            <ToggleButton value="changed">
              It tests something else now
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>
      )}
    </Box>
  );
}
VersionPreview.propTypes = {
  rework: PropTypes.bool,
};
