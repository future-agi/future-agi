import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Selection strip for the scenario table.
 *
 * Sits flush with the table's own column-header row and mirrors its
 * styling (background.neutral, divider seams, uppercase micro-typography
 * for the state label) so the strip reads as an extension of the table
 * rather than a widget stapled on top. When nothing is selected there is
 * no bar; when a row is checked, the same visual band gains a status
 * label on the left and a few text actions on the right.
 */
export default function SelectionBar({ count, onDelete, onClear }) {
  const focusBuilder = () => {
    const el = document.querySelector(
      'input[placeholder="Reply to the builder…"], textarea[placeholder="Reply to the builder…"]',
    );
    if (el && typeof el.focus === "function") el.focus();
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2.5, py: 1,
        bgcolor: "background.neutral",
        borderTop: "1px solid", borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {/* status label — uppercase micro-caps to match the table's own
          column headers, so this reads as another header row. */}
      <Stack direction="row" alignItems="baseline" spacing={0.75}>
        <Typography
          sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle",
                textTransform: "uppercase", letterSpacing: 0.4 }}
        >
          Selection
        </Typography>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {count}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          of your scenarios
        </Typography>
      </Stack>

      <Box flex={1} />

      {/* actions — text buttons, right-aligned, matched weight so the
          reader can pick the one they want without hunting. */}
      <Button
        size="small"
        onClick={focusBuilder}
        startIcon={<Iconify icon="solar:pen-new-square-linear" width={13} />}
        sx={{
          typography: "s2", fontWeight: 600,
          color: "text.primary", px: 1, minWidth: 0,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        Edit in builder
      </Button>

      <Button
        size="small"
        onClick={onDelete}
        startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={13} />}
        sx={{
          typography: "s2", fontWeight: 600,
          color: "#DC2626", px: 1, minWidth: 0,
          "&:hover": { bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.12 : 0.06) },
        }}
      >
        Delete
      </Button>

      <Box sx={{ width: 1, height: 18, bgcolor: "divider", mx: 0.5 }} />

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
