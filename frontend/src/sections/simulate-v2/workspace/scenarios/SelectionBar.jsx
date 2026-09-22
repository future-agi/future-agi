import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Bulk-action bar for the scenario table.
 *
 * Sits IN-PLACE of the search / group-by / filter toolbar when one
 * or more rows are checked. Reuses the search toolbar's exact
 * frame — same background, padding, height and button styling —
 * so the row does not visually jump when selection toggles; only
 * the contents swap.
 *
 * Hierarchy (left → right):
 *   ✓  2 selected · Clear ..............  [Edit]  [Delete]
 *   context                                secondary  destructive
 *
 * Run + Repeats stay anchored to the workspace header (top-right).
 */
export default function SelectionBar({ count, onEdit, onDelete, onClear }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 2.5, py: 1.25, minHeight: 52 }}
    >
      {/* Context — count on the left, Clear as its own chip button
          on the right of the pair so people notice it. Text-forward
          "Clear selection" with a leading × icon reads as an action,
          not a passive label. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
        <Box sx={{
          width: 18, height: 18, borderRadius: 0.5,
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: "primary.main",
          color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
        }}>
          <Iconify icon="eva:checkmark-fill" width={13} />
        </Box>
        <Typography sx={{
          typography: "s2", fontWeight: 700, fontSize: 13,
          color: "text.primary", whiteSpace: "nowrap",
          fontVariantNumeric: "tabular-nums",
        }}>
          {count} selected
        </Typography>
        <Button
          size="small"
          onClick={onClear}
          startIcon={<Iconify icon="eva:close-fill" width={13} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12.5,
            textTransform: "none",
            color: "text.primary",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            px: 1.25, py: 0.25, minWidth: 0,
            ml: 0.5,
            "& .MuiButton-startIcon": { mr: 0.5 },
            "&:hover": {
              borderColor: (t) => alpha(t.palette.text.primary, 0.4),
              bgcolor: "action.hover",
            },
          }}
        >
          Clear selection
        </Button>
      </Stack>

      <Box sx={{ flex: 1 }} />

      {/* Actions — match the search-row button styling exactly so
          the two states of this row read as siblings. */}
      {onEdit && (
        <Button
          size="small"
          variant="outlined"
          onClick={onEdit}
          startIcon={<Iconify icon="solar:chat-round-line-linear" width={14} />}
          sx={{
            typography: "s2", fontWeight: 700, textTransform: "none",
            color: "text.primary", borderColor: "divider",
          }}
        >
          Edit
        </Button>
      )}
      <Button
        size="small"
        variant="outlined"
        onClick={onDelete}
        startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={14} />}
        sx={{
          typography: "s2", fontWeight: 700, textTransform: "none",
          color: "error.main",
          borderColor: (t) => alpha(t.palette.error.main, 0.4),
          "&:hover": {
            borderColor: "error.main",
            bgcolor: (t) => alpha(t.palette.error.main, t.palette.mode === "dark" ? 0.1 : 0.05),
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
  onEdit: PropTypes.func,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
