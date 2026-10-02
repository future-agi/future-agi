import React from "react";
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import CustomJsonViewer from "./CustomJsonCellViewer";

const JsonCellRenderer = ({
  isHover,
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
}) => {
  const isBlankString = typeof value === "string" && value.trim() === "";
  const hasRenderableValue =
    value !== null && value !== undefined && !isBlankString;
  let parsedJson = value;
  let shouldRenderPlainText = false;

  if (typeof value === "string") {
    if (isBlankString) {
      parsedJson = null;
    } else {
      try {
        parsedJson = JSON.parse(value);
      } catch (err) {
        shouldRenderPlainText = true;
      }
    }
  }

  return (
    <CustomTooltip
      show={Boolean(valueReason?.length)}
      title={formattedValueReason()}
      enterDelay={500}
      enterNextDelay={500}
      leaveDelay={100}
      arrow
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          justifyContent: metadata?.responseTimeMs ? "space-between" : "start",
          padding: "4px 8px",
          fontFamily: "monospace",
        }}
      >
        <Box
          sx={{
            maxHeight: "100%",
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            scrollbarWidth: "none",
            msOverflowStyle: "none",
            "&::-webkit-scrollbar": {
              display: "none",
            },
          }}
        >
          {hasRenderableValue &&
            (shouldRenderPlainText ? (
              <Box component="span">{value}</Box>
            ) : (
              <CustomJsonViewer object={parsedJson} />
            ))}
        </Box>

        {isHover && (
          <Box
            onClick={(e) => e.stopPropagation()}
            onMouseEnter={(e) => e.stopPropagation()}
            onMouseLeave={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <RenderMeta
              originType={originType}
              meta={metadata}
              valuesInfo={valueInfos}
            />
          </Box>
        )}
      </Box>
    </CustomTooltip>
  );
};

JsonCellRenderer.propTypes = {
  isHover: PropTypes.bool,
  value: PropTypes.any,
  valueReason: PropTypes.array,
  formattedValueReason: PropTypes.func,
  originType: PropTypes.string,
  metadata: PropTypes.object,
  valueInfos: PropTypes.any,
};

export default React.memo(JsonCellRenderer);
