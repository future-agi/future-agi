import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

/**
 * A small glyph after the row number when a row fails the dataset's
 * enforced schema (R2-SPEC P6 §3.4; `row_meta.validation{ok, issues:[{path,
 * message}]}`): amber `mdi:alert-outline`, the issues in the tooltip.
 * Renders nothing for valid rows or when no schema is enforced.
 *
 * Props (frozen, R2 manifest row 35):
 * @param {object} [props.rowMeta]  the grid row's `row_meta`
 */
const RowIssuesGlyph = ({ rowMeta }) => {
  const v = rowMeta?.validation;
  if (!v || v.ok || !v.issues?.length) return null;
  const n = v.issues.length;
  return (
    <CustomTooltip
      show
      size="small"
      placement="right"
      arrow
      title={
        <Stack gap={0.25}>
          <Typography typography="s3" fontWeight={600}>
            {`${n} schema ${n === 1 ? "issue" : "issues"}`}
          </Typography>
          {v.issues.slice(0, 5).map((i) => (
            <Typography key={`${i.path}:${i.message}`} typography="s3">
              {i.message}
            </Typography>
          ))}
          {n > 5 && <Typography typography="s3">{`+${n - 5} more`}</Typography>}
        </Stack>
      }
    >
      <Box
        component="span"
        aria-label={`${n} schema ${n === 1 ? "issue" : "issues"}`}
        data-testid="row-issues-glyph"
        sx={{
          display: "inline-flex",
          ml: 0.5,
          color: "amber.500",
          cursor: "help",
        }}
      >
        <Iconify icon="mdi:alert-outline" width={14} />
      </Box>
    </CustomTooltip>
  );
};

RowIssuesGlyph.propTypes = { rowMeta: PropTypes.object };

export default RowIssuesGlyph;
