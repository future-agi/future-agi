import React, { useRef, useState } from "react";
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";
import RenderMeta from "../RenderMeta";
import GridValueSummary from "src/sections/common/Values/GridValueSummary";
import useColumnViewMode, {
  useCellColumnId,
} from "src/sections/common/Values/useColumnViewMode";
import EmptyDash, { isEmptyCellValue } from "./EmptyDash";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import CopyButton from "src/sections/common/ux/CopyButton";
import { jsonChipSummary } from "./jsonChips";

/** `{…}` as indented JSON for the card (string input parsed when it can). */
const prettyJson = (value, max = 4000) => {
  let text;
  try {
    const v = typeof value === "string" ? JSON.parse(value) : value;
    text = JSON.stringify(v, null, 2);
  } catch {
    text = String(value);
  }
  return text.length > max ? `${text.slice(0, max)}\n…` : text;
};

/** The chip line (`lookup_order(order_id) +1`). */
const JsonChips = ({ summary }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}>
    <Box
      component="span"
      sx={{
        px: 0.75,
        py: 0.125,
        borderRadius: 0.75,
        bgcolor: "action.hover",
        typography: "s3",
        fontFamily: "monospace",
        color: "text.primary",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        minWidth: 0,
      }}
    >
      {summary.text}
    </Box>
    {summary.more > 0 && (
      <Box
        component="span"
        sx={{ typography: "s3", color: "text.secondary", flexShrink: 0 }}
      >
        +{summary.more}
      </Box>
    )}
  </Box>
);
JsonChips.propTypes = { summary: PropTypes.object };

/**
 * One view (A1-08, depth in place): the cell's chips for everyone; after
 * 400 ms of hover a card shows the formatted JSON with `Copy JSON`. The
 * card never opens while the button is down (a click opens the row, a drag
 * selects cells); Space (peek) and the row view still show the raw value.
 */
const JsonHover = ({ value, children }) => {
  const [pressed, setPressed] = useState(false);
  return (
    <Box
      sx={{ height: "100%", minWidth: 0 }}
      onMouseDown={() => setPressed(true)}
      onMouseEnter={(e) => setPressed(e.buttons !== 0)}
      onMouseLeave={() => setPressed(false)}
    >
      <HoverCard
        component="div"
        delay={400}
        title="JSON"
        maxWidth={420}
        trigger={children}
        triggerSx={{ height: "100%", minWidth: 0, borderRadius: 0 }}
        // The cell keeps its own click (open the row) and focus (AG Grid).
        // Without the button role the trigger has no expanded state either
        // (a11y #10: aria-expanded is not allowed on a plain div).
        triggerProps={{
          onClick: undefined,
          tabIndex: -1,
          role: undefined,
          "aria-expanded": undefined,
        }}
        cardProps={{ style: pressed ? { display: "none" } : undefined }}
        actions={
          <CopyButton
            value={() => prettyJson(value, Infinity)}
            tooltip="Copy JSON"
          />
        }
      >
        <Box
          component="pre"
          sx={{
            m: 0,
            maxHeight: 280,
            overflow: "auto",
            typography: "s3",
            fontFamily: "monospace",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "text.primary",
          }}
        >
          {prettyJson(value)}
        </Box>
      </HoverCard>
    </Box>
  );
};
JsonHover.propTypes = { value: PropTypes.any, children: PropTypes.node };

const JsonCellRenderer = ({
  isHover,
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
}) => {
  // L7b G8: the column's View as (JSON / YAML / Text) wins over the chips;
  // Auto keeps the chip line.
  const rootRef = useRef(null);
  const columnId = useCellColumnId(rootRef);
  const [viewMode] = useColumnViewMode(columnId);
  const chips = viewMode === "auto" ? jsonChipSummary(value) : null;
  const isBlankString = typeof value === "string" && value.trim() === "";
  const hasRenderableValue =
    value !== null && value !== undefined && !isBlankString;

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
        ref={rootRef}
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          justifyContent: metadata?.response_time_ms
            ? "space-between"
            : "start",
          padding: "4px 8px",
        }}
      >
        <Box
          sx={{
            height: "100%",
            minHeight: 0,
            overflow: "hidden",
            wordBreak: "break-word",
          }}
        >
          {hasRenderableValue && isEmptyCellValue(value) && <EmptyDash />}
          {hasRenderableValue && !isEmptyCellValue(value) && (
            <JsonHover value={value}>
              {chips ? (
                <JsonChips summary={chips} />
              ) : (
                <GridValueSummary
                  value={value}
                  dataType="json"
                  bare
                  fallback={
                    <Box component="span" sx={{ display: "block", fontSize: 12, lineHeight: "18px" }}>
                      {typeof value === "string"
                        ? value
                        : JSON.stringify(value)}
                    </Box>
                  }
                />
              )}
            </JsonHover>
          )}
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
