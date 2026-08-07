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
  // Only render a clock when the source value actually carried a time. A
  // date-only string like "2026-01-29" has no time component, so appending
  // "00:00" shows data the cell never held. Strings with a time (ISO "T" or an
  // "HH:mm" part) keep the clock, including a genuine midnight (#1766).
  const hasTimeComponent =
    typeof value === "string" ? /\d{1,2}:\d{2}/.test(value) : true;

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
          format(
            new Date(value),
            hasTimeComponent ? "dd/MM/yyyy HH:mm" : "dd/MM/yyyy",
          )
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
