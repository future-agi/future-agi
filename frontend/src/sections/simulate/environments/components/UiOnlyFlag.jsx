import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { UI_ONLY_FEATURES } from "../uiOnlyFeatures";

export const UI_ONLY_TOOLTIP =
  "UI only: this flow isn't wired to the API yet. It runs on sample data and builds nothing.";

// A muted "UI only" pill on every part of the design that has no API yet.
// `feature` names its UI_ONLY_FEATURES entry, so the tooltip says what the
// backend needs to wire it; an entry's `note` replaces the sample-data line.
export default function UiOnlyFlag({ feature, sx }) {
  const { ask, note = UI_ONLY_TOOLTIP } = UI_ONLY_FEATURES[feature] ?? {};
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={ask ? `${note} Needs: ${ask}.` : note}
    >
      <Box
        component="span"
        aria-label="UI only"
        sx={{
          px: 0.625,
          height: 18,
          display: "inline-flex",
          alignItems: "center",
          borderRadius: "4px",
          fontSize: 11,
          lineHeight: "16px",
          fontWeight: "fontWeightSemiBold",
          whiteSpace: "nowrap",
          flexShrink: 0,
          color: "text.secondary",
          bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
          ...sx,
        }}
      >
        UI only
      </Box>
    </CustomTooltip>
  );
}
UiOnlyFlag.propTypes = { feature: PropTypes.string, sx: PropTypes.object };
