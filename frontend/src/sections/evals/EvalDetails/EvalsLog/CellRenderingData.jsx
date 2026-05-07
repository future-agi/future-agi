import React, { useEffect, useRef } from "react";
import { Box, Chip, Skeleton, Typography } from "@mui/material";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import _ from "lodash";
import CustomTooltip from "src/components/tooltip";
import FormattedValueReason from "../../EvaluationsTabs/FormattedReason";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { format } from "date-fns";
import SvgColor from "src/components/svg-color";
import GridIcon from "src/components/gridIcon/GridIcon";
import CustomHeaderAgGridSort from "src/components/custom-header-ag-grid-sort/CustomHeaderAgGridSort";
import TestAudioPlayer from "../../../../components/custom-audio/TestAudioPlayer";
import NumericCell from "../../../common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

export const EvaluateArrayCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        padding: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
      }}
    >
      <Box
        sx={{
          lineHeight: "1.5",
          flex: 1,
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          overflow: "hidden",
        }}
      >
        {value?.map((item) => (
          <Chip
            key={item}
            label={item}
            size="small"
            variant="outlined"
            color="primary"
          />
        ))}
      </Box>
    </Box>
  );
};

export const EvaluateCell = ({ value, dataType, cellData }) => {
  const getScorePercentage = (s, decimalPlaces = 0) => {
    if (s <= 0) s = 0;
    const score = s * 100;
    return Number(score.toFixed(decimalPlaces));
  };
  const hasRenderableValue = (cellValue) =>
    cellValue !== undefined && cellValue !== null && cellValue !== "";

  if (Array.isArray(value?.output)) {
    return <EvaluateArrayCellRenderer value={value?.output} />;
  }

  if (cellData?.output_type === OutputTypes.NUMERIC) {
    return <NumericCell value={value?.output} />;
  }

  if (cellData?.output_type == "choices") {
    return <EvaluateArrayCellRenderer value={value?.output} />;
  }

  if (dataType == "choices") {
    return <EvaluateArrayCellRenderer value={value} />;
  }

  if (dataType === "boolean") {
    const bgColor = value
      ? value?.output === "Failed"
        ? interpolateColorBasedOnScore(0, 1)
        : interpolateColorBasedOnScore(1, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.secondary",
          // flex: 1,
          height: "100%",
        }}
      >
        {_.capitalize(value?.output)}
      </Box>
    );
  }
  if (dataType === "float" || cellData?.output_type == "score") {
    const hasValue = hasRenderableValue(cellData?.cell_value);
    const bgColor = hasValue
      ? interpolateColorBasedOnScore(value?.output, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.primary",
          height: "100%",
        }}
      >
        {hasValue ? `${getScorePercentage(value?.output)}%` : ""}
      </Box>
    );
  }

  if (dataType === "array") {
    return <EvaluateArrayCellRenderer value={value} />;
  }

  return (
    <Box
      sx={{
        padding: 1,
        whiteSpace: "pre-wrap",
        lineHeight: "1.5",
        overflow: "hidden",
        textOverflow: "ellipsis",
        display: "-webkit-box",
        WebkitLineClamp: "6",
        WebkitBoxOrient: "vertical",
      }}
    >
      {typeof value === "object"
        ? Array.isArray(value?.output)
          ? ""
          : value?.output
        : value}
    </Box>
  );
};

const ImageRenderer = ({ value, setImageUrl }) => {
  return (
    <Box sx={{ height: "100%", width: "100%", display: "flex" }}>
      {value ? (
        <GridIcon
          height="100%"
          src={value}
          alt=""
          onClick={(e) => {
            e.stopPropagation();
            setImageUrl?.(value);
          }}
          onMouseEnter={(e) => {
            window.__imageClick = true;
            e.stopPropagation();
          }}
          onMouseLeave={(e) => {
            window.__imageClick = false;
            e.stopPropagation();
          }}
          sx={{
            cursor: "pointer",
            borderRadius: "8px",
            maxWidth: "180px",
            width: "100%",
            height: "100%",
          }}
        />
      ) : (
        <Box
          sx={{
            height: "100%",
            flex: 1,
            maxWidth: 110,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "var(--bg-input)",
            borderRadius: "8px",
            border: "1px dashed var(--border-default)",
          }}
          onClick={(e) => {
            e.stopPropagation();
          }}
        >
          <img
            src="/assets/placeholder.svg"
            alt="No image placeholder"
            style={{
              width: "24px",
              height: "24px",
              opacity: 0.6,
            }}
          />
        </Box>
      )}
    </Box>
  );
};

ImageRenderer.propTypes = {
  value: PropTypes.any,
  setImageUrl: PropTypes.func,
};

const showSearched = (value, cellData) => {
  if (value) {
    if (!cellData.keyExists) {
      return value;
    } else if (cellData.keyExists) {
      const stInd = cellData?.startIndex || 0;
      const endInd = cellData?.endIndex || 0;

      if (stInd < 0 || endInd > value?.length || stInd >= endInd) {
        return value;
      }

      const MAX_PREFIX_CHARS = 50;
      const MIN_PREFIX_CHARS = 20;

      const beforeHighlight = value.slice(0, stInd);
      const highlighted = value.slice(stInd, endInd);
      const afterHighlight = value.slice(endInd);

      const shouldTrimPrefix = beforeHighlight.length > MAX_PREFIX_CHARS;

      let displayPrefix = beforeHighlight;
      if (shouldTrimPrefix) {
        displayPrefix = "..." + beforeHighlight.slice(-MIN_PREFIX_CHARS);
      }
      const marked = `<span style="background-color: yellow; font-weight: bold;">${highlighted}</span>`;

      return (
        <CellMarkdown text={`${displayPrefix}${marked}${afterHighlight}`} />
      );
    }
  } else {
    return "";
  }
};

export const CustomCellRender = (props) => {
  const dataType = props?.column?.colDef?.data_type;
  const originType = props?.column?.colDef?.origin_type;
  const outputType = props?.column?.colDef?.col?.output_type;
  const value = props?.value;
  const cellData = props?.data?.[props?.column?.colId] || {};
  const status = cellData?.status?.toLowerCase();
  const valueReason = value?.reason?.toString();
  const rowData = props?.data;
  const headerName = props?.column?.colDef?.col?.name || "";
  const cellType = rowData?.input_data_types?.[headerName];

  const { setImageUrl } = useSingleImageViewContext();

  if (headerName === "Created At") {
    return (
      <Box sx={{ padding: 1, whiteSpace: "pre-wrap", lineHeight: "1.5" }}>
        {value ? format(new Date(value), "yyyy-MM-dd") : "Invalid Date"}
      </Box>
    );
  }

  if (status === "processing" || value === undefined) {
    return (
      <Box
        sx={{
          paddingX: 1,
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Skeleton sx={{ width: "100%", height: "20px" }} variant="rounded" />
      </Box>
    );
  }
  if (cellType) {
    if (cellType === "image") {
      return <ImageRenderer value={value} setImageUrl={setImageUrl} />;
    } else if (cellType === "audio") {
      const cacheKey = `wavesurfer-${rowData?.logId}-${props?.column?.colDef?.col?.id}-${value}`;
      return (
        <Box
          className="audio-control-btn"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => {
            window.__audioClick = true;
            e.stopPropagation();
          }}
          onMouseEnter={() => (window.__audioClick = true)}
          onMouseLeave={() => (window.__audioClick = false)}
          sx={{
            height: "100%",
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <AudioPlaybackProvider>
            <TestAudioPlayer
              audioData={{ url: value }}
              showFileName={false}
              allowSinglePlayback={true}
              cacheKey={cacheKey}
            />
          </AudioPlaybackProvider>
        </Box>
      );
    }
  }

  if (status === "error") {
    return (
      <CustomTooltip
        show={Boolean(valueReason?.length)}
        title={FormattedValueReason(valueReason)}
        enterDelay={500}
        arrow
        expandable
      >
        <Box
          sx={{
            color: "error.main",
            opacity: 1,
            flex: 1,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Typography variant="body2" align="center">
            Error
          </Typography>
        </Box>
      </CustomTooltip>
    );
  }

  if (originType === "evaluation" || originType === "optimisation_evaluation") {
    return (
      <CustomTooltip
        show={Boolean(valueReason?.length)}
        title={FormattedValueReason(valueReason)}
        enterDelay={500}
        enterNextDelay={500}
        arrow
        expandable
      >
        <Box sx={{ height: "100%" }}>
          <EvaluateCell
            cellData={{ ...cellData, outputType: outputType }}
            value={value}
            dataType={dataType}
            originType={originType}
          />
        </Box>
      </CustomTooltip>
    );
  }

  if (Array.isArray(value)) {
    return <EvaluateArrayCellRenderer value={value} />;
  }

  switch (dataType) {
    case "boolean":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {cellData?.cell_value?.toString()}
        </Box>
      );
    case "text":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? showSearched(
                cellData?.cell_value?.output || cellData?.cell_value?.input,
                cellData,
              )
            : Array.isArray(cellData?.cell_value)
              ? cellData?.cell_value.map((value, index) => (
                  <span style={{ whiteSpace: "pre-wrap" }} key={index}>
                    {showSearched(value, cellData)}
                    <br />
                  </span>
                ))
              : showSearched(cellData?.cell_value, cellData)}
        </Box>
      );
    case "array":
      break;
    case "choices":
      return (
        <Box
          sx={{
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object" ? (
            Array.isArray(cellData?.cell_value) ? (
              <Box sx={{ height: "100%" }}>
                <EvaluateCell
                  cellData={cellData}
                  value={cellData?.cell_value}
                  dataType={dataType}
                  originType={originType}
                />
              </Box>
            ) : (
              showSearched(cellData?.cell_value?.output, cellData)
            )
          ) : (
            showSearched(cellData?.cell_value, cellData)
          )}
        </Box>
      );
    case "rule_string":
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? Array.isArray(cellData?.cell_value)
              ? cellData?.cell_value.map((value, index) => (
                  <span key={index}>
                    {showSearched(value, cellData)}
                    <br />
                  </span>
                ))
              : showSearched(
                  cellData?.cell_value?.output || cellData?.cell_value?.input,
                  cellData,
                )
            : showSearched(cellData?.cell_value, cellData)}
        </Box>
      );
    default:
      return (
        <Box
          sx={{
            padding: 1,
            whiteSpace: "pre-wrap",
            lineHeight: "1.5",
            overflow: "hidden",
            textOverflow: "ellipsis",
            display: "-webkit-box",
            WebkitLineClamp: "6",
            WebkitBoxOrient: "vertical",
          }}
        >
          {typeof cellData?.cell_value == "object"
            ? showSearched(cellData?.cell_value?.output, cellData)
            : showSearched(cellData?.cell_value, cellData)}
        </Box>
      );
  }
};

EvaluateCell.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  cellData: PropTypes.object,
  originType: PropTypes.string,
};

EvaluateArrayCellRenderer.propTypes = {
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  value: PropTypes.any,
};

CustomCellRender.propTypes = {
  column: PropTypes.object,
  value: PropTypes.any,
  data: PropTypes.object,
  node: PropTypes.any,
  colDef: PropTypes.any,
};

export const CustomDevelopDetailColumn = (props) => {
  const { displayName, col, api } = props;
  const refContainer = useRef(null);

  const colDef = props?.column?.colDef;

  useEffect(() => {
    if (api && col) {
      const minWidth = Math.max(displayName.length * 8 + 50, 250);
      props.api.setColumnWidths([{ key: col.id, newWidth: minWidth }]);
    }
  }, [api, col, displayName]);

  const renderIcon = () => {
    if (colDef?.headerName === "image_url") {
      return <Iconify icon="material-symbols:image-outline" />;
    } else if (col.origin_type === "run_prompt") {
      return <Iconify icon="token:rune" sx={{ color: "info.main" }} />;
    } else if (col.origin_type === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "#22B3B7" }}
        />
      );
    } else if (
      col.origin_type === "optimisation" ||
      col.origin_type === "optimisation_evaluation"
    ) {
      return (
        <Iconify
          icon="icon-park-outline:smart-optimization"
          sx={{ color: "primary.main" }}
        />
      );
    } else if (col.origin_type === "annotation_label") {
      return <Iconify icon="jam:write" />;
    } else if (col.data_type === "text") {
      return (
        <SvgColor
          src="/assets/icons/navbar/ic_new_text.svg"
          sx={{ width: "20px", height: "20px" }}
        />
      );
    } else if (col.data_type === "array") {
      return <Iconify icon="material-symbols:data-array" />;
    } else if (col.data_type === "integer") {
      return <Iconify icon="material-symbols:tag" />;
    } else if (col.data_type === "float") {
      return <Iconify icon="tabler:decimal" />;
    } else if (col.data_type === "boolean") {
      return <Iconify icon="material-symbols:toggle-on-outline" />;
    } else if (col.data_type === "datetime") {
      return <Iconify icon="tabler:calendar" />;
    } else if (col.data_type === "json") {
      return <Iconify icon="material-symbols:data-object" />;
    } else if (col.data_type === "image") {
      return <Iconify icon="material-symbols:image-outline" />;
    } else {
      return (
        <SvgColor
          src="/assets/icons/navbar/ic_new_text.svg"
          sx={{ width: "20px", height: "20px" }}
        />
      );
    }
  };

  const getBackgroundColor = (_originType) => {
    // if (
    //   originType === "evaluation" ||
    //   originType === "optimisation_evaluation"
    // ) {
    //   return "#EEFDFE"; // Same color as evaluation
    // } else if (originType === "run_prompt") {
    //   return "#EEF4FF";
    // } else if (originType === "optimisation") {
    //   return "primary.lighter";
    // } else if (originType === "annotation_label") {
    //   return "#FFE2FE";
    // }
    return "background.default";
  };

  return (
    <Box
      ref={refContainer}
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        backgroundColor: getBackgroundColor(colDef?.origin_type),
        paddingX: 2,
        height: "100%",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {renderIcon()}
        <Typography fontWeight={700} fontSize="13px" color={"text.secondary"}>
          {displayName}
        </Typography>
        <CustomHeaderAgGridSort
          ref={refContainer}
          column={props.column}
          setSort={props.setSort}
        />
      </Box>
    </Box>
  );
};

CustomDevelopDetailColumn.propTypes = {
  displayName: PropTypes.string.isRequired,
  eSort: PropTypes.object,
  eMenu: PropTypes.object,
  eFilterButton: PropTypes.object,
  eFilter: PropTypes.object,
  eSortOrder: PropTypes.object,
  eSortAsc: PropTypes.object,
  eSortDesc: PropTypes.object,
  eSortNone: PropTypes.object,
  eText: PropTypes.object,
  menuButtonRef: PropTypes.object,
  filterButtonRef: PropTypes.object,
  sortOrderRef: PropTypes.object,
  sortAscRef: PropTypes.object,
  sortDescRef: PropTypes.object,
  sortNoneRef: PropTypes.object,
  filterRef: PropTypes.object,
  showColumnMenu: PropTypes.func,
  col: PropTypes.object,
  column: PropTypes.object,
  hideMenu: PropTypes.bool,
  eGridHeader: PropTypes.any,
  api: PropTypes.any,
  setSort: PropTypes.func,
};
