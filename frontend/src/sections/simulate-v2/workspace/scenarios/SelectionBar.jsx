import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { injectComposerScaffold } from "../../_mock/composerScaffoldBus";

/**
 * Bulk-action bar for the scenario table.
 *
 * Shape: count on the left, a strip of clickable suggestion chips
 * in the middle, Clear + Delete on the right. Clicking a chip pins
 * that suggestion into the builder composer as a scaffold — same
 * shape Falcon's ChatInput uses for its detected-skill chips — so
 * the user can add more text before sending.
 */

/*
  Prompt suggestions surfaced next to the count. Handpicked to
  match the kind of instruction a user typically wants applied
  across many scenarios at once. Not exhaustive — three is enough
  to demo the pattern without wrapping in narrow panes.
*/
const SUGGESTIONS = [
  "Make callers more impatient",
  "Add a sub-goal for delivery verification",
  "Rewrite the tone to be firmer",
];

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
      {/* count */}
      <Typography sx={{ typography: "s2", color: "text.primary", flexShrink: 0 }}>
        <Box component="span" sx={{ fontWeight: 700 }}>{count}</Box>
        {" "}
        <Box component="span" sx={{ color: "text.secondary" }}>
          {count === 1 ? "scenario selected" : "scenarios selected"}
        </Box>
      </Typography>

      {/* suggestion chips — click to pin into the composer */}
      <Stack
        direction="row" alignItems="center" spacing={0.75}
        sx={{
          pl: 2, ml: 0.5, minWidth: 0, flex: 1,
          borderLeft: "1px solid", borderColor: "divider",
          flexWrap: "wrap", rowGap: 0.75,
        }}
      >
        {SUGGESTIONS.map((s) => (
          <SuggestionChip key={s} label={s} onClick={() => injectComposerScaffold(s)} />
        ))}
      </Stack>

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

/**
 * One suggestion chip. Same visual weight as the "chips" row that
 * StudioConsole renders above an empty composer — subtle text-on-
 * divider pill that highlights on hover, so the reader recognises
 * both surfaces as "clickable prompt suggestions".
 */
function SuggestionChip({ label, onClick }) {
  return (
    <Button
      size="small"
      onClick={onClick}
      sx={{
        typography: "s3", fontWeight: 500,
        color: "text.secondary",
        border: "1px solid", borderColor: "divider",
        borderRadius: 999, px: 1.25, py: 0.25,
        minWidth: 0, textTransform: "none",
        "&:hover": {
          borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.5 : 0.35),
          color: "#7857FC",
          bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.1 : 0.05),
        },
      }}
    >
      {label}
    </Button>
  );
}
SuggestionChip.propTypes = { label: PropTypes.string, onClick: PropTypes.func };
