import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SELECTED_EVAL_SHAPE } from "./evalEntry";
import CustomTooltip from "src/components/tooltip";
import { EvalEntryChips, EvalMappingChips } from "./evalEntryCells";

// One row of the Evaluations tab for a backend-backed environment: the entry
// as `evaluations.selected[]` sends it — name, description,
// Library/Custom, the cost line and Vel's chips for what fills each input — plus the
// caller's Remove action. Removing stops future grading only: every verdict
// it already produced stays on its call and is still shown there, marked.
export default function SelectedEvalRow({ item, action }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
      <Box
        sx={{
          width: 30, height: 30, borderRadius: 0.875,
          display: "grid", placeItems: "center", flexShrink: 0,
          color: "text.secondary", bgcolor: "background.neutral",
        }}
      >
        <Iconify icon="solar:shield-check-linear" width={16} />
      </Box>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
            {item.name}
          </Typography>
          <EvalEntryChips entry={item} />
        </Stack>
        {/* One line, as Vel's; the whole description on hover. */}
        {item.description && (
          <CustomTooltip show arrow size="small" placement="bottom-start" title={item.description}>
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {item.description}
            </Typography>
          </CustomTooltip>
        )}
        <Box sx={{ mt: 0.75 }}>
          <EvalMappingChips entry={item} />
        </Box>
      </Box>
      <Box sx={{ flexShrink: 0 }}>{action}</Box>
    </Stack>
  );
}

SelectedEvalRow.propTypes = { item: SELECTED_EVAL_SHAPE, action: PropTypes.node };
