import React from "react";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";
import { getColorMap } from "src/sections/workbench/createPrompt/VersionHistory/common";
import RenderMeta from "../RenderMeta";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";

/**
 * `enum` cells (R2-SPEC P6): the value as a coloured pill (Prompts'
 * label colours, `getColorMap`), one colour per value.
 *
 * Props (frozen): commonPropTypes + `isHover`, `cellData`.
 */
const EnumCellRenderer = ({
  isHover,
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
}) => {
  const theme = useTheme();
  const text = value == null ? "" : String(value).trim();
  const colors = text ? getColorMap(text, theme) : null;
  return (
    <CustomTooltip
      show={Boolean(valueReason?.length)}
      title={formattedValueReason?.()}
      enterDelay={500}
      arrow
      slotProps={tooltipSlotProp}
    >
      <Box sx={{ px: 1, py: 0.75, height: "100%", minWidth: 0 }}>
        {text && (
          <Box
            component="span"
            sx={{
              display: "inline-block",
              maxWidth: "100%",
              px: 1,
              borderRadius: 0.75,
              typography: "s3",
              fontWeight: 500,
              lineHeight: "20px",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              bgcolor: colors?.backgroundColor,
              color: colors?.color,
            }}
          >
            {text}
          </Box>
        )}
        {isHover && (
          <RenderMeta
            originType={originType}
            meta={metadata}
            valuesInfo={valueInfos}
          />
        )}
      </Box>
    </CustomTooltip>
  );
};

EnumCellRenderer.propTypes = {
  ...commonPropTypes,
  isHover: PropTypes.bool,
  cellData: PropTypes.object,
};

export default React.memo(EnumCellRenderer);
