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
  across many scenarios at once. One is enough for the demo —
  keeps the bar readable and lets the chip carry visual weight.
*/
const SUGGESTIONS = [
  "Make callers more impatient",
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
 * One suggestion chip. Filled purple tint + sparkle icon so it reads
 * as "an AI suggestion you can click", not a static tag. Trailing
 * arrow reinforces the clickability. Hover deepens the fill and
 * slides the arrow — small motion is what makes a button feel like
 * a button.
 */
function SuggestionChip({ label, onClick }) {
  return (
    <Button
      size="small"
      onClick={onClick}
      startIcon={<Iconify icon="solar:magic-stick-3-bold" width={13} />}
      endIcon={
        <Iconify
          icon="solar:arrow-right-linear"
          width={12}
          sx={{ transition: "transform 160ms ease" }}
          className="chip-arrow"
        />
      }
      sx={{
        typography: "s3", fontWeight: 600,
        color: "#7857FC",
        border: "1px solid",
        borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.45 : 0.3),
        bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
        borderRadius: 999,
        px: 1.5, py: 0.35,
        minWidth: 0, textTransform: "none",
        transition: "background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease",
        "& .MuiButton-startIcon": { mr: 0.5 },
        "& .MuiButton-endIcon": { ml: 0.5 },
        "&:hover": {
          borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.7 : 0.55),
          bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.22 : 0.14),
          boxShadow: (t) => `0 0 0 3px ${alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.12)}`,
          "& .chip-arrow": { transform: "translateX(2px)" },
        },
      }}
    >
      {label}
    </Button>
  );
}
SuggestionChip.propTypes = { label: PropTypes.string, onClick: PropTypes.func };
