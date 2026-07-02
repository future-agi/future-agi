import React from "react";
import { Box } from "@mui/material";
import { format } from "date-fns";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import GenerateDiffText from "../../GenerateDiffText";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";

const DatetimeCellRenderer = ({
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
}) => {
  const isValueArray = Array.isArray(value);
  const isBlankValue = value === null || value === undefined || value === "";
  const isValidDate = !isBlankValue && !isNaN(new Date(value).getTime());

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
      <Box sx={{ padding: 1, whiteSpace: "pre-wrap", lineHeight: "1.5" }}>
        {isValueArray ? (
          <GenerateDiffText cellText={value} />
        ) : isValidDate ? (
          format(new Date(value), "dd/MM/yyyy HH:mm")
        ) : isBlankValue ? (
          ""
        ) : (
          "Invalid Date"
        )}
        <RenderMeta originType={originType} meta={metadata} />
      </Box>
    </CustomTooltip>
  );
};

DatetimeCellRenderer.propTypes = {
  ...commonPropTypes,
};

export default React.memo(DatetimeCellRenderer);
