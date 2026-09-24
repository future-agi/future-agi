import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const TOOLTIP =
  "Sample data — the backend doesn't provide this field yet, so a placeholder is shown. It fills in with real data once the API does.";

// A small amber "Sample" pill for a workspace section whose value was mock-filled
// (env.provenance[field] === "mock"), so real vs placeholder is never ambiguous.
// Sibling to DummyHeaderLabel's grey "Dummy" pill for the My-Environments columns.
export default function MockBadge({ label = "Sample" }) {
  return (
    <CustomTooltip show arrow size="small" title={TOOLTIP}>
      <Box
        component="span"
        sx={{
          px: 0.625,
          py: 0.125,
          borderRadius: 0.75,
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: "warning.dark",
          bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
          flexShrink: 0,
        }}
      >
        {label}
      </Box>
    </CustomTooltip>
  );
}

MockBadge.propTypes = { label: PropTypes.string };
