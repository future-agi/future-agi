import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PersonaBadge } from "../../components/primitives";
import { clickable, focusRing } from "../../components/clickable";

/*
  One compact scenario row, used by the add-scenario pickers. Lives in its
  own file (it used to be exported from ScenariosStep) so the pickers don't
  import the whole tab — that import made a cycle that stopped hot updates
  from reaching the page.
*/
export default function ScenarioRow({
  row,
  index,
  onRemove,
  selectable,
  checked,
  onToggle,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        ...focusRing,
        px: 2.5,
        py: 1,
        cursor: selectable ? "pointer" : "default",
        "&:hover": selectable ? { bgcolor: "action.hover" } : {},
      }}
      {...clickable(selectable ? onToggle : undefined, {
        role: "checkbox",
        checked,
      })}
    >
      {selectable ? (
        <Iconify
          icon={checked ? "solar:check-square-bold" : "solar:stop-linear"}
          width={20}
          sx={{
            color: checked ? "primary.main" : "text.tertiary",
            flexShrink: 0,
          }}
        />
      ) : (
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            width: 20,
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {index + 1}
        </Typography>
      )}

      <Box sx={{ flex: 1.4, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
          {row.title}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {row.task}
        </Typography>
      </Box>

      {/* Wide enough for a full job title — roles were truncating at 180. */}
      <Box
        sx={{ width: 240, flexShrink: 0, display: { xs: "none", md: "block" } }}
      >
        <PersonaBadge persona={row.persona} compact />
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ width: 62, flexShrink: 0, display: { xs: "none", sm: "flex" } }}
      >
        <Iconify
          icon="solar:chat-round-line-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          ~{row.turns}
        </Typography>
      </Stack>

      {onRemove && (
        <IconButton
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          <Iconify
            icon="solar:close-circle-linear"
            width={16}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      )}
    </Stack>
  );
}
ScenarioRow.propTypes = {
  row: PropTypes.object,
  index: PropTypes.number,
  onRemove: PropTypes.func,
  selectable: PropTypes.bool,
  checked: PropTypes.bool,
  onToggle: PropTypes.func,
};
