import React from "react";
import { Box } from "@mui/material";
import { format, parseISO } from "date-fns";
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
  const isISODateOnly =
    typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  const hasTimeComponent =
    typeof value !== "string" || /(?:T|\s)\d{1,2}:\d{2}/.test(value);
  const parsedDate = isISODateOnly ? parseISO(value) : new Date(value);

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
          format(parsedDate, hasTimeComponent ? "dd/MM/yyyy HH:mm" : "dd/MM/yyyy")
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
