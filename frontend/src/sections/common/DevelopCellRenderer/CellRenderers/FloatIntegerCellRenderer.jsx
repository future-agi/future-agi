import React from "react";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import GenerateDiffText from "../../GenerateDiffText";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";
import { sniffValue } from "src/sections/common/Values/sniff";
import { MismatchCorner } from "src/sections/common/Values/GridValueSummary";

const FloatIntegerCellRenderer = ({
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
}) => {
  const isValueArray = Array.isArray(value);
  const isInvalidValue =
    value === null ||
    value === undefined ||
    (!isValueArray && typeof value === "number" && isNaN(value));

  if (isInvalidValue) return "";
  // R2 (P6): text in a number column gets the amber corner.
  const mismatch =
    !isValueArray && typeof value === "string"
      ? sniffValue(value, { declaredType: "float" }).meta?.mismatch
      : null;

  return (
    <CustomTooltip
      show={Boolean(valueReason?.length)}
      title={formattedValueReason()}
      enterDelay={500}
      enterNextDelay={500}
      leaveDelay={100}
      arrow
      slotProps={tooltipSlotProp}
    >
      <Box
        sx={{
          padding: 1,
          whiteSpace: "pre-wrap",
          lineHeight: "1.5",
          position: "relative",
          height: "100%",
        }}
      >
        {mismatch && <MismatchCorner text={mismatch} />}
        {isValueArray ? <GenerateDiffText cellText={value} /> : value}
        <RenderMeta
          originType={originType}
          meta={metadata}
          valuesInfo={valueInfos}
        />
      </Box>
    </CustomTooltip>
  );
};

FloatIntegerCellRenderer.propTypes = {
  ...commonPropTypes,
};

export default React.memo(FloatIntegerCellRenderer);
