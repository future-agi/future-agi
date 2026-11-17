import React, { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import TextArrayJsonCellRenderer from "./TextArrayJsonCellRenderer";
import { commonPropTypes } from "./cellRendererHelper";
import {
  checkTypeMeta,
  checkTypeOf,
  hasDraft,
  parseChecks,
} from "src/sections/develop-detail/TaskSuite/checkTypes";
import { parseJson } from "src/sections/develop-detail/TaskSuite/itemModel";

export const DraftChip = () => (
  <Box
    component="span"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      height: 18,
      px: 0.625,
      borderRadius: 0.5,
      typography: "s3",
      fontWeight: 600,
      flexShrink: 0,
      bgcolor: (t) => alpha(t.palette.amber[500], 0.16),
      color: (t) =>
        t.palette.mode === "dark" ? t.palette.amber[500] : t.palette.amber[700],
    }}
  >
    Draft
  </Box>
);

/**
 * `checks` cells (R2-SPEC §7.3.6): `5 checks` and an amber `Draft` chip
 * while any of them is unreviewed; hover lists each check with its type.
 *
 * Props (frozen): commonPropTypes + `isHover`, `cellData`.
 */
const ChecksCellRenderer = (props) => {
  const { value } = props;
  const checks = useMemo(() => parseChecks(value), [value]);
  if (!Array.isArray(parseJson(value)))
    return <TextArrayJsonCellRenderer {...props} />;
  const n = checks.length;
  const draft = hasDraft(checks);
  const list = (
    <Stack gap={0.5} sx={{ py: 0.25, maxWidth: 320 }}>
      {checks.map((c) => (
        <Stack
          key={c.key ?? c.name}
          direction="row"
          gap={1}
          alignItems="baseline"
        >
          <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }} noWrap>
            {c.name ?? c.key}
          </Typography>
          <Typography typography="s3" color="text.secondary" noWrap>
            {checkTypeMeta(checkTypeOf(c)).chip}
            {c.draft ? " · Draft" : ""}
          </Typography>
        </Stack>
      ))}
      {draft && (
        <Typography typography="s3" color="text.secondary">
          Drafted at import · review them in the Row view
        </Typography>
      )}
    </Stack>
  );
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        height: "100%",
        px: 1,
        minWidth: 0,
      }}
      data-testid="checks-cell"
    >
      <CustomTooltip
        show={n > 0}
        size="small"
        placement="bottom-start"
        title={list}
      >
        <Typography
          typography="s2"
          noWrap
          color={n ? "text.primary" : "text.disabled"}
        >
          {n ? `${n} check${n === 1 ? "" : "s"}` : "No checks"}
        </Typography>
      </CustomTooltip>
      {draft && <DraftChip />}
    </Box>
  );
};

ChecksCellRenderer.propTypes = commonPropTypes;

export default ChecksCellRenderer;
