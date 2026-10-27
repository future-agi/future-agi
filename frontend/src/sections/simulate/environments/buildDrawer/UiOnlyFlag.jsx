import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

export const UI_ONLY_TOOLTIP =
  "UI only: this flow isn't wired to the API yet. It runs on sample data and builds nothing.";

// A muted "UI only" pill, sibling to the list's grey "Dummy" pill, on every way
// in that is the design without its API yet.
export default function UiOnlyFlag({ sx }) {
  return (
    <CustomTooltip show arrow size="small" title={UI_ONLY_TOOLTIP}>
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
UiOnlyFlag.propTypes = { sx: PropTypes.object };
