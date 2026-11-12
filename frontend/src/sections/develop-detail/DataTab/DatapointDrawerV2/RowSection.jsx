import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CopyButton from "src/sections/common/ux/CopyButton";
import { fmtCount } from "src/sections/common/format/fmt";
import useSectionTranslate from "src/sections/common/Collab/useSectionTranslate";

/**
 * A titled, foldable section of the Row tab: `Input 1`, `Generated columns
 * on this dataset 1`, … with "Copy section as JSON".
 */
const RowSection = ({
  title,
  count,
  open,
  onToggle,
  copyJson,
  copyCount,
  hint,
  children,
  testId,
}) => {
  // W4-8: "Translate section" over the section's fields (by name).
  const names = copyJson ? Object.keys(copyJson() ?? {}) : [];
  const translate = useSectionTranslate(names);
  return (
    <Box data-testid={testId} sx={{ minWidth: 0 }}>
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 0.75 }}>
        <ButtonBase
          onClick={onToggle}
          aria-expanded={open}
          sx={{
            gap: 0.5,
            borderRadius: 0.5,
            px: 0.25,
            ml: -0.25,
            color: "text.primary",
          }}
        >
          <Iconify
            icon={open ? "eva:chevron-down-fill" : "eva:chevron-right-fill"}
            width={16}
            sx={{ color: "text.disabled" }}
          />
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            {title}
          </Typography>
          {Number.isFinite(count) && (
            <Typography typography="s2" color="text.disabled">
              {count}
            </Typography>
          )}
        </ButtonBase>
        <Box sx={{ flex: 1 }} />
        {translate.menu}
        {copyJson && (
          // RV#24 + RV#14: same contrast as the field icons; check in place.
          <CopyButton
            value={() => JSON.stringify(copyJson(), null, 2)}
            tooltip={`Copy section as JSON${
              Number.isFinite(copyCount)
                ? ` (${fmtCount(copyCount, "field")})`
                : ""
            }`}
            iconSize={15}
            sx={{ p: 0.5 }}
          />
        )}
      </Stack>
      {hint && (
        <Box sx={{ mb: 0.75, mt: -0.25 }}>
          {typeof hint === "string" ? (
            <Typography typography="s2" color="text.secondary">
              {hint}
            </Typography>
          ) : (
            hint
          )}
        </Box>
      )}
      <Collapse in={open} timeout={120} unmountOnExit>
        <Stack gap={1}>
          {children}
          {translate.block}
        </Stack>
      </Collapse>
    </Box>
  );
};

RowSection.propTypes = {
  title: PropTypes.string.isRequired,
  count: PropTypes.number,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  copyJson: PropTypes.func,
  /** Fields in the copied JSON (the tooltip says `(2 fields)`). */
  copyCount: PropTypes.number,
  hint: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
};

export default RowSection;
