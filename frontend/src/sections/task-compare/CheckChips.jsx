import React from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const isJudge = (c) =>
  c?.kind === "judge" || c?.type === "judge" || c?.key === "reply_rubric";

/** `Order found: lookup_order returned the order · judged by an LLM`. */
export function checkTooltip(c) {
  const name = c?.name ?? c?.key ?? "Check";
  const desc = c?.description && c.description !== name ? c.description : "";
  return `${name}${desc ? `: ${desc}` : ""}${isJudge(c) ? " · judged by an LLM" : ""}`;
}

/**
 * A task's checks as small monospace chips (`order_found`), the check's name
 * and description on hover. One line by default: chips that don't fit drop
 * whole onto a clipped second line, never cut mid-name. Used by the side by
 * side Expected strip and the results grid's Expected column.
 */
export default function CheckChips({ checks, wrap = false, sx }) {
  const list = (checks ?? []).filter((c) => c && (c.key || c.name));
  if (!list.length) return null;
  return (
    <Stack
      direction="row"
      spacing={0.5}
      data-check-chips
      sx={{
        minWidth: 0,
        overflow: "hidden",
        flexShrink: 1,
        flexWrap: "wrap",
        height: wrap ? "auto" : 20,
        rowGap: 0.5,
        ...sx,
      }}
    >
      {list.map((c) => (
        <CustomTooltip
          describeChild
          key={c.key ?? c.name}
          show
          size="small"
          placement="bottom"
          title={checkTooltip(c)}
        >
          <Box
            component="span"
            tabIndex={0}
            sx={{
              px: 0.75,
              height: 20,
              lineHeight: "20px",
              borderRadius: "10px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              typography: "s3",
              fontFamily: "monospace",
              whiteSpace: "nowrap",
              flexShrink: 0,
              color: "text.secondary",
            }}
          >
            {c.key ?? c.name}
          </Box>
        </CustomTooltip>
      ))}
    </Stack>
  );
}

CheckChips.propTypes = {
  checks: PropTypes.array,
  wrap: PropTypes.bool,
  sx: PropTypes.object,
};
