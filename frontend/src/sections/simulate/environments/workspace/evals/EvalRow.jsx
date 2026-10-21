import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { EVAL_SHAPE } from "./evals.constants";

// One eval, shared by the Suggested and Added lists: an icon tile, name +
// category, blurb, an optional pass threshold and a caller-supplied action
// (Add on Suggested, remove on Added).
export default function EvalRow({ item, action, dense }) {
  return (
    <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: dense ? 1.25 : 1.5 }}>
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: 0.875,
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
          color: "text.secondary",
          bgcolor: "background.neutral",
        }}
      >
        <Iconify icon={item.icon || "solar:shield-check-linear"} width={16} />
      </Box>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
            {item.name}
          </Typography>
          {item.category && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
              · {item.category}
            </Typography>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {item.blurb}
        </Typography>
      </Box>
      {item.threshold != null && !dense && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            flexShrink: 0,
            display: { xs: "none", md: "block" },
            fontVariantNumeric: "tabular-nums",
          }}
        >
          pass ≥ {(item.threshold * 100).toFixed(0)}%
        </Typography>
      )}
      <Box sx={{ flexShrink: 0 }}>{action}</Box>
    </Stack>
  );
}

EvalRow.propTypes = {
  item: EVAL_SHAPE,
  action: PropTypes.node,
  dense: PropTypes.bool,
};
