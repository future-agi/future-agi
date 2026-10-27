import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const TINY = { height: 18, fontSize: 11, "& .MuiChip-label": { px: 0.75 } };

// Stands in for a field the API doesn't send yet. The chip names what's
// coming ("Owner soon"), dashed so it never reads as data; the tooltip says
// it in full. `plural` for a name like "Status counts", so it reads "aren't".
// `tiny` fits a column header or a dense row.
export default function SoonChip({
  label,
  what,
  plural = false,
  size = "small",
  sx,
}) {
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={`${what} ${plural ? "aren't" : "isn't"} in the API yet`}
    >
      <Chip
        label={`${label} soon`}
        size="small"
        variant="outlined"
        sx={{
          flexShrink: 0,
          borderStyle: "dashed",
          color: "text.secondary",
          ...(size === "tiny" ? TINY : null),
          ...sx,
        }}
      />
    </CustomTooltip>
  );
}
SoonChip.propTypes = {
  label: PropTypes.string.isRequired,
  what: PropTypes.string.isRequired,
  plural: PropTypes.bool,
  size: PropTypes.oneOf(["small", "tiny"]),
  sx: PropTypes.object,
};
