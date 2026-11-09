import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { MutedText, cellBoxSx, cellPropTypes } from "./cellCommon";
import { isCompactCell, compactCellSx } from "./ExampleCells";
import { productionCellText, productionOf } from "../retestModel";

/**
 * iterate r2 G3 (P1): how the row did in production, next to the re-test
 * result — `✗ Failed · Resolution · turn 5` (the judge's reason on hover).
 */
export const ProductionCell = ({ data, context }) => {
  const p = productionOf(data);
  if (!p)
    return (
      <Stack sx={isCompactCell(context) ? compactCellSx : cellBoxSx}>
        <MutedText>Not from production</MutedText>
      </Stack>
    );
  const failed = String(p.outcome ?? "").toLowerCase() === "fail";
  const compact = isCompactCell(context);
  return (
    <Stack
      sx={isCompactCell(context) ? compactCellSx : cellBoxSx}
      data-testid="production-cell"
    >
      <CustomTooltip
        show={Boolean(p.reason || (compact && p.reply))}
        title={[
          compact ? productionCellText(p) : null,
          p.session ?? null,
          p.reason ?? null,
        ]
          .filter(Boolean)
          .join(" · ")}
        size="small"
        placement="top"
        arrow
      >
        <Typography
          typography="s2"
          fontWeight={500}
          noWrap
          sx={{ color: failed ? "red.600" : "green.600", minWidth: 0 }}
        >
          {/* IT-R5 G3 (S1): one line (compact rows) = what production
              replied; the criterion and turn are on hover. */}
          {compact && p.reply
            ? `${failed ? "✗" : "✓"} “${p.reply}”`
            : productionCellText(p)}
        </Typography>
      </CustomTooltip>
      {/* IT-R5 G3 (S1): what production replied, beside the run's new
          reply (Output); the judge's reason stays on hover. */}
      {!isCompactCell(context) && (p.reply || p.reason) ? (
        <Typography
          typography="s2"
          color={p.reply ? "text.primary" : "text.secondary"}
          data-testid="production-reply"
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {p.reply ? `“${p.reply}”` : p.reason}
        </Typography>
      ) : null}
      {!isCompactCell(context) && p.session ? (
        <Box
          component="span"
          data-testid="production-session"
          sx={{
            alignSelf: "flex-start",
            mt: 0.25,
            px: 0.5,
            borderRadius: 0.5,
            bgcolor: "action.hover",
            fontFamily: "monospace",
            typography: "s3",
            color: "text.secondary",
          }}
        >
          {p.session}
        </Box>
      ) : null}
    </Stack>
  );
};
ProductionCell.propTypes = cellPropTypes;
