import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const TINY = { height: 18, fontSize: 11, "& .MuiChip-label": { px: 0.75 } };

// Stands in for a field the API doesn't send yet. The chip names what's
// coming ("Owner soon"), dashed so it never reads as data; the tooltip says
// it in full, says the field is new in this design (nothing that showed before
// is missing), and `detail` explains the field itself where its name alone
// doesn't. `plural` for a name like "Status counts", so it reads "aren't".
// `tiny` fits a column header or a dense row.
export default function SoonChip({
  label,
  what,
  detail,
  plural = false,
  size = "small",
  sx,
}) {
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={
        <>
          <Box component="span" sx={{ display: "block", fontWeight: 600 }}>
            {`${what} ${plural ? "aren't" : "isn't"} in the API yet`}
          </Box>
          {detail && (
            <Box component="span" sx={{ display: "block", mt: 0.5 }}>
              {detail}
            </Box>
          )}
          <Box component="span" sx={{ display: "block", mt: 0.5, opacity: 0.8 }}>
            New in this design. Nothing that showed here before is missing; it fills in once the API sends it.
          </Box>
        </>
      }
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
  // What the field means, when its name alone doesn't say.
  detail: PropTypes.string,
  plural: PropTypes.bool,
  size: PropTypes.oneOf(["small", "tiny"]),
  sx: PropTypes.object,
};
