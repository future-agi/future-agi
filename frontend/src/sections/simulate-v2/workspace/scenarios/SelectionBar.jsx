import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Bulk-action bar for the scenario table.
 *
 * Appears when one or more rows are checked. Two actions — Delete
 * and Clear — plus a hint pointing at the builder chat, because
 * bulk-editing scenario copy is easier as a natural-language
 * instruction than as a mass form. Neutral card treatment matching
 * the rest of the feature's cards; no accent fill.
 */
export default function SelectionBar({ count, onDelete, onClear }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 2, py: 1.25,
        borderRadius: 1.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        mb: 1.5,
      }}
    >
      {/* count + label */}
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box
          sx={{
            width: 22, height: 22, borderRadius: "50%",
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: "text.primary", color: "background.paper",
          }}
        >
          <Iconify icon="solar:check-read-linear" width={13} />
        </Box>
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          <Box component="span" sx={{ fontWeight: 700 }}>{count}</Box>
          {" "}
          <Box component="span" sx={{ color: "text.secondary" }}>
            {count === 1 ? "scenario selected" : "scenarios selected"}
          </Box>
        </Typography>
      </Stack>

      {/* hint pointing at the builder */}
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{ pl: 2, ml: 0.5, borderLeft: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="solar:chat-round-line-linear" width={14} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Type an instruction in the builder to edit them together
        </Typography>
      </Stack>

      <Box flex={1} />

      {/* actions */}
      <Tooltip arrow title="Deselect all">
        <Button
          size="small"
          onClick={onClear}
          sx={{
            typography: "s2", fontWeight: 600,
            color: "text.secondary", minWidth: 0, px: 1,
            "&:hover": { color: "text.primary", bgcolor: "transparent" },
          }}
        >
          Clear
        </Button>
      </Tooltip>

      <Button
        size="small"
        onClick={onDelete}
        startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
        sx={{
          typography: "s2", fontWeight: 600,
          color: "#DC2626",
          px: 1, minWidth: 0,
          "&:hover": { bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.12 : 0.06) },
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
