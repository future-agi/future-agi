import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Bulk-action bar for the scenario table.
 *
 * Appears when one or more rows are checked. Two direct actions —
 * Delete and Clear — and a hint pointing at the builder chat, because
 * bulk-editing scenario copy is easier as a natural-language
 * instruction than as a mass form ("make these callers older", "add a
 * sub-goal to each one that verifies delivery date"). The chat lives
 * on the left of the workspace; the hint carries an arrow to make the
 * connection obvious the first time.
 */
export default function SelectionBar({ count, onDelete, onClear }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2, py: 1.25,
        border: "1px solid", borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.2 : 0.16),
        borderRadius: 1.5,
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
        mb: 1.5,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            width: 22, height: 22, borderRadius: 0.75, display: "grid", placeItems: "center",
            bgcolor: "text.primary", color: "background.paper",
            typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
          }}
        >
          {count}
        </Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {count === 1 ? "scenario selected" : "scenarios selected"}
        </Typography>
      </Stack>

      <Box sx={{ height: 18, borderLeft: "1px solid", borderColor: "divider" }} />

      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Iconify icon="solar:arrow-left-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          Type an instruction in the builder to edit them together
        </Typography>
      </Stack>

      <Box flex={1} />

      <Tooltip arrow title="Deselect all">
        <Button
          size="small"
          onClick={onClear}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", minWidth: 0 }}
        >
          Clear
        </Button>
      </Tooltip>

      <Button
        size="small"
        variant="outlined"
        onClick={onDelete}
        startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
        sx={{
          typography: "s2", fontWeight: 700,
          color: "#DC2626",
          borderColor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.5 : 0.4),
          "&:hover": {
            borderColor: "#DC2626",
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.06),
          },
        }}
      >
        Delete
      </Button>
    </Stack>
  );
}
SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
