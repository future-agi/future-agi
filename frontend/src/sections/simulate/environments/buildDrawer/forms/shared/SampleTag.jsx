import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const SAMPLE_TOOLTIP =
  "Sample data: this isn't read from your account yet. It shows what the flow will list.";

/** A muted "Sample" pill beside anything a UI-only form shows as dummy data. */
export default function SampleTag({ sx }) {
  return (
    <CustomTooltip show arrow size="small" title={SAMPLE_TOOLTIP}>
      <Box
        component="span"
        aria-label="Sample data"
        sx={{
          px: 0.625,
          height: 18,
          display: "inline-flex",
          alignItems: "center",
          borderRadius: "4px",
          fontSize: 11,
          lineHeight: "16px",
          fontWeight: 600,
          whiteSpace: "nowrap",
          flexShrink: 0,
          color: "text.secondary",
          bgcolor: (t) => alpha(t.palette.text.primary, 0.08),
          ...sx,
        }}
      >
        Sample
      </Box>
    </CustomTooltip>
  );
}
SampleTag.propTypes = { sx: PropTypes.object };
