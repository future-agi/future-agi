import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Selection strip for the scenario table.
 *
 * Reads as one continuous strip: a monochrome "N selected" pill on
 * the left, an icon-button toolbar on the right, and the whole thing
 * sits on the neutral surface so it distinguishes from the table
 * (background.paper) without shouting. No accent fill, no separate
 * hint sentence — the pen icon carries the edit affordance and the
 * builder chip on the composer says the rest.
 */
export default function SelectionBar({ count, onDelete, onClear }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0}
      sx={{
        pl: 1, pr: 0.5, py: 0.5,
        borderRadius: 999,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        width: "fit-content",
        mb: 1.5,
      }}
    >
      {/* count pill — plain, monochrome */}
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ pr: 1, pl: 0.5 }}>
        <Box
          sx={{
            width: 6, height: 6, borderRadius: "50%",
            bgcolor: "text.primary",
          }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {count} <Box component="span" sx={{ fontWeight: 500, color: "text.secondary" }}>selected</Box>
        </Typography>
      </Stack>

      {/* action toolbar — icon buttons, tooltip labels */}
      <Box sx={{ width: 1, height: 20, bgcolor: "divider", mx: 0.5 }} />

      <ActionButton
        icon="solar:pen-new-square-linear"
        label="Edit with the builder"
        hint="Type an instruction in the chat on the left"
        onClick={() => {
          const el = document.querySelector('input[placeholder="Reply to the builder…"], textarea[placeholder="Reply to the builder…"]');
          if (el && typeof el.focus === "function") el.focus();
        }}
      />
      <ActionButton
        icon="solar:trash-bin-trash-linear"
        label="Delete selected"
        tint="#DC2626"
        onClick={onDelete}
      />

      <Box sx={{ width: 1, height: 20, bgcolor: "divider", mx: 0.5 }} />

      <Tooltip arrow title="Clear selection">
        <IconButton
          size="small"
          onClick={onClear}
          sx={{ p: 0.5, color: "text.subtitle", "&:hover": { color: "text.primary" } }}
        >
          <Iconify icon="solar:close-circle-linear" width={15} />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
SelectionBar.propTypes = {
  count: PropTypes.number.isRequired,
  onDelete: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};

function ActionButton({ icon, label, hint, tint, onClick }) {
  return (
    <Tooltip
      arrow
      title={
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 700 }}>{label}</Typography>
          {hint && (
            <Typography sx={{ typography: "s3", opacity: 0.75 }}>{hint}</Typography>
          )}
        </Box>
      }
    >
      <IconButton
        size="small"
        onClick={onClick}
        sx={{
          p: 0.5,
          color: tint || "text.secondary",
          "&:hover": {
            color: tint || "text.primary",
            bgcolor: (t) => tint
              ? alpha(tint, t.palette.mode === "dark" ? 0.12 : 0.06)
              : "action.hover",
          },
        }}
      >
        <Iconify icon={icon} width={15} />
      </IconButton>
    </Tooltip>
  );
}
ActionButton.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  hint: PropTypes.string,
  tint: PropTypes.string,
  onClick: PropTypes.func,
};

