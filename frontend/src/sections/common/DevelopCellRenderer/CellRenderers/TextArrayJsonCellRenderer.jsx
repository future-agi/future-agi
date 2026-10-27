import React, { useMemo } from "react";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import GenerateDiffText from "../../GenerateDiffText";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import CellMarkdown from "../../CellMarkdown";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { isJsonValue } from "src/utils/utils";

function truncateContent(content, indices = [], maxLength = 100) {
  const numIndices = indices.length || 1; // Avoid divide by zero
  const sliceLength = Math.max(10, Math.floor(maxLength / numIndices));

  return content.map((con) =>
    typeof con === "string" && con.length > maxLength
      ? con.slice(0, sliceLength) + "..."
      : con,
  );
}

const TextArrayJsonCellRenderer = ({
  isHover,
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
  cellData,
}) => {
  const isValueArray = Array.isArray(value);
  const parsedJson = useMemo(() => {
    if (!isJsonValue(value)) return null;

    try {
      return typeof value === "string" ? JSON.parse(value) : value;
    } catch {
      return null;
    }
  }, [value]);

  const renderSearchValue = () => {
    if (typeof value !== "string" || !Array.isArray(cellData?.indices)) {
      return value;
    }

    const indices = cellData.indices
      .filter(
        ([start, end]) => start >= 0 && end <= value.length && start < end,
      )
      .sort((a, b) => a[0] - b[0]); // Sort to ensure proper order

    if (indices.length === 0) return value;

    let content = [];
    let lastIndex = 0;

    indices.forEach(([start, end], i) => {
      // Non-highlighted text before this highlight
      if (lastIndex < start) {
        content.push(value.slice(lastIndex, start));
      }

      // Highlighted text
      content.push(
        <span
          key={`highlight-${i}`}
          style={{ backgroundColor: "var(--score-yellow)", fontWeight: "bold" }}
        >
          {value.slice(start, end)}
        </span>,
      );

      lastIndex = end;
    });

    // Add the remaining text
    if (lastIndex < value.length) {
      content.push(value.slice(lastIndex));
    }

    content = truncateContent(content, cellData?.indices);

    return <>{content}</>;
  };

  // Added a small check to see if the value is string then it can be rendered directly but if it is not
  // try to stringify it and if it is not a valid JSON then return an empty string
  const renderValueDirectly = (v) => {
    if (typeof v === "string") {
      return value;
    }
    try {
      return JSON.stringify(v);
    } catch {
      return "";
    }
  };

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
          display: "flex",
          flexDirection: "column",
          height: "100%",
          minHeight: 0,
          justifyContent: metadata?.responseTimeMs ? "space-between" : "start",
          padding: "4px 8px",
        }}
      >
        {value && (
          <Box
            sx={{
              whiteSpace: "pre-wrap",
              lineHeight: "1.5",
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
            }}
          >
            {isValueArray ? (
              <GenerateDiffText cellText={value} />
            ) : cellData?.keyExists ? (
              renderSearchValue()
            ) : originType === "run_prompt" && isJsonValue(value) ? (
              <CustomJsonViewer object={parsedJson} clickToExpandNode />
            ) : originType === "evaluation_reason" ||
              originType === "run_prompt" ? (
              <CellMarkdown text={value} />
            ) : (
              renderValueDirectly(value)
            )}
          </Box>
        )}

        <ShowComponent condition={isHover}>
          <RenderMeta
            originType={originType}
            meta={metadata}
            valuesInfo={valueInfos}
          />
        </ShowComponent>
      </Box>
    </CustomTooltip>
  );
};

TextArrayJsonCellRenderer.propTypes = {
  ...commonPropTypes,
  cellData: PropTypes.object,
};

export default React.memo(TextArrayJsonCellRenderer);
