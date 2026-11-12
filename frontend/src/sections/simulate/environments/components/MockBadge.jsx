import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";

const TOOLTIP =
  "Sample data: the backend doesn't provide this field yet, so a placeholder is shown. It fills in with real data once the API does.";

// A small warning "Sample" badge (shared StatusBadge) for a workspace section whose value was mock-filled
// (env.provenance[field] === "mock"), so real vs placeholder is never ambiguous.
// Sibling to DummyHeaderLabel's grey "Dummy" pill for the My-Environments columns.
export default function MockBadge({ label = "Sample" }) {
  return (
    <CustomTooltip show arrow size="small" title={TOOLTIP}>
      <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
        <StatusBadge status="warning" role="warning" label={label} variant="badge" size="sm" dot={false} />
      </Box>
    </CustomTooltip>
  );
}

MockBadge.propTypes = { label: PropTypes.string };
