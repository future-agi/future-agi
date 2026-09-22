import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Bulk-action bar for the scenario table.
 *
 * Renders IN-PLACE where the search / group-by / filter toolbar
 * normally lives — same row of the SectionCard, same height, same
 * horizontal padding. When one or more scenarios are checked, the
 * toolbar transforms into this bar; when nothing is checked, the
 * toolbar returns. It's the Gmail / GitHub-changes-page pattern:
 * one location for both states, so the actions appear exactly
 * where the user's eye already is.
 *
 * The parent card is sticky, so as the user scrolls through many
 * scenarios the transformed bar stays pinned at the top of the
 * list without ever occluding a row.
 *
 * Hierarchy (left → right):
 *   [count]  Clear      |    Edit    Delete    [▶ Run N selected]
 *   context  utility         secondary  destructive     PRIMARY
 */
export default function SelectionBar({ count, onRun, onEdit, onDelete, onClear }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        width: "100%",
        px: 2.5, py: 1.25,
        minHeight: 52,
        /* Subtle tint so the swap is legible ("something is selected")
           but the row still reads as a toolbar, not a floating alert. */
        bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.08 : 0.05),
      }}
    >
      {/* CONTEXT — how many are selected */}
      <Stack direction="row" alignItems="center" spacing={0.875} sx={{ flexShrink: 0 }}>
        <Box sx={{
          width: 22, height: 22, borderRadius: "50%",
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: "text.primary",
          color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
          fontSize: 11, fontWeight: 700, fontVariantNumeric: "tabular-nums",
        }}>
          {count}
        </Box>
        <Typography sx={{
          typography: "s2", fontWeight: 600, fontSize: 13,
          color: "text.primary", whiteSpace: "nowrap",
        }}>
          {count === 1 ? "scenario selected" : "scenarios selected"}
        </Typography>
      </Stack>

      {/* Clear — utility */}
      <Tooltip arrow title="Deselect all">
        <Button
          size="small"
          onClick={onClear}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12,
            color: "text.subtitle", minWidth: 0, px: 1, py: 0.375,
            "&:hover": { color: "text.primary", bgcolor: "action.hover" },
          }}
        >
          Clear
        </Button>
      </Tooltip>

      {/* Push the actions to the right — mirrors how the Table/List
          toggle sits at the far right in the normal toolbar, so the
          swap feels like the same layout wearing a different hat. */}
      <Box sx={{ flex: 1 }} />

      {/* SECONDARY — Edit with builder chat */}
      {onEdit && (
        <Tooltip arrow title="Send this selection to the builder chat">
          <Button
            size="small"
            onClick={onEdit}
            variant="outlined"
            startIcon={<Iconify icon="solar:chat-round-line-linear" width={13} />}
            sx={{
              typography: "s2", fontWeight: 600, fontSize: 12.5,
              color: "text.primary",
              borderColor: "divider",
              px: 1.5, py: 0.5, minWidth: 0,
              whiteSpace: "nowrap",
              "&:hover": {
                borderColor: (t) => alpha(t.palette.text.primary, 0.4),
                bgcolor: "action.hover",
              },
            }}
          >
            Edit
          </Button>
        </Tooltip>
      )}

      {/* DESTRUCTIVE — Delete */}
      <Tooltip arrow title="Delete selected scenarios">
        <Button
          size="small"
          onClick={onDelete}
          variant="outlined"
          startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
          sx={{
            typography: "s2", fontWeight: 600, fontSize: 12.5,
            color: "#DC2626",
            borderColor: (t) => alpha("#DC2626", 0.4),
            px: 1.5, py: 0.5, minWidth: 0,
            whiteSpace: "nowrap",
            "&:hover": {
              borderColor: "#DC2626",
              bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
            },
          }}
        >
          Delete
        </Button>
      </Tooltip>

      {/* PRIMARY — Run N selected */}
      {onRun && (
        <Button
          size="small"
          variant="contained"
          onClick={onRun}
          startIcon={<Iconify icon="solar:play-bold" width={13} />}
          sx={{
            typography: "s2", fontWeight: 700, fontSize: 12.5,
            px: 1.75, py: 0.5, minWidth: 0,
            bgcolor: "text.primary",
            color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
            boxShadow: "none",
            whiteSpace: "nowrap",
            "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
          }}
        >
          Run {count} selected
        </Button>
      )}
    </Stack>
  );
}
SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  onRun: PropTypes.func,
  onEdit: PropTypes.func,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
