import PropTypes from "prop-types";
import { IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

// Above the chat composer while rows are ticked on the Scenarios tab, so the
// next message reads as an edit of those rows (Vel's selection chip).
export default function SelectionContextChip({
  count,
  rows = [],
  all = false,
  onClear,
}) {
  const preview = all
    ? "all matching"
    : rows
        .slice(0, 2)
        .map((r) => r.title || r.name || "scenario")
        .join(", ");
  const rest = !all && count > 2 ? ` +${count - 2}` : "";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "action.hover",
      }}
    >
      <Iconify
        icon="solar:layers-minimalistic-linear"
        width={14}
        sx={{ color: "text.subtitle", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        Editing <b>{count}</b> scenario{count === 1 ? "" : "s"}: {preview}
        {rest}
      </Typography>
      <CustomTooltip show size="small" arrow title="Clear selection">
        <IconButton size="small" aria-label="Clear selection" onClick={onClear}>
          <Iconify
            icon="solar:close-circle-linear"
            width={14}
            sx={{ color: "text.subtitle" }}
          />
        </IconButton>
      </CustomTooltip>
    </Stack>
  );
}

SelectionContextChip.propTypes = {
  count: PropTypes.number.isRequired,
  rows: PropTypes.array,
  all: PropTypes.bool,
  onClear: PropTypes.func,
};
