import { Box, Chip } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { useCompositeEvalStore } from "src/sections/develop-detail/states";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import RenderMeta from "../RenderMeta";
import EvaluateArrayCellRenderer from "./EvaluateArrayCellRenderer";
import NumericCell from "./NumericCell";
import { OutputTypes } from "../CellRenderers/cellRendererHelper";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";
const getScorePercentage = (s, decimalPlaces = 0) => {
  if (s <= 0) s = 0;
  const score = s * 100;
  return Number(score.toFixed(decimalPlaces));
};

const hasRenderableValue = (value) =>
  value !== undefined && value !== null && value !== "";

// Normalise the `value_infos` blob into an object regardless of whether
// the caller passed camelCase, snake_case, or a JSON string. The cell
// layer upstream is inconsistent across grids (the backend returns a
// dict on most endpoints but a string on a few legacy paths).
const parseValueInfos = (cellData) => {
  const raw = cellData?.value_infos ?? cellData?.valueInfos;
  if (!raw) return null;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return raw;
};

//Being used in experiments & Compare datasets
//In compare dataset we are not getting choices map
const EvaluateCell = ({
  value,
  dataType,
  meta,
  isFutureAgiEval,
  cellData,
  originType,
  choicesMap,
  outputType,
}) => {
  const output = cellData?.valueInfos?.output || outputType;

  // Detect composite eval cells. The Phase B runner writes a `composite_id`
  // key and a `children` array into `value_infos` alongside the aggregate
  // score. Use either as a liveness signal so both newer (composite_id)
  // and older snapshots render drill-down.
  const parsedValueInfos = React.useMemo(
    () => parseValueInfos(cellData),
    [cellData],
  );

  const isComposite = Boolean(
    parsedValueInfos?.composite_id ||
      (Array.isArray(parsedValueInfos?.children) &&
        parsedValueInfos.children.length > 0 &&
        parsedValueInfos.children[0]?.child_id),
  );
  const setCompositeEval = useCompositeEvalStore((s) => s.setCompositeEval);

  const compositeBadge = isComposite ? (
    <Chip
      size="small"
      icon={<Iconify icon="mdi:graph-outline" width={12} />}
      label={`${parsedValueInfos?.children?.length ?? 0} children`}
      onClick={(e) => {
        window.__compositeEvalClick = true;
        e.stopPropagation();
        setCompositeEval(parsedValueInfos);
      }}
      sx={{
        ml: 0.75,
        height: 20,
        fontSize: "10px",
        fontWeight: 600,
        cursor: "pointer",
        "& .MuiChip-icon": { marginLeft: "4px", marginRight: "-4px" },
      }}
    />
  ) : null;

  if (output === OutputTypes.NUMERIC) {
    return <NumericCell value={value} />;
  }
  if (output === OutputTypes.SCORE) {
    const result = parsedValueInfos?.data?.result;

    if (hasRenderableValue(result) && !Number.isNaN(result)) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            p: 1,
            height: "100%",
            maxWidth: "100%",
          }}
        >
          <Chip
            label={result}
            size="small"
            variant="outlined"
            sx={{
              borderColor: "purple.500",
              color: "purple.500",
              fontWeight: 500,
              maxWidth: 240,
              "& .MuiChip-label": {
                overflow: "hidden",
                textOverflow: "ellipsis",
              },
            }}
          />
        </Box>
      );
    }
  }
  if (dataType === "boolean") {
    const bgColor = value
      ? value === "Failed"
        ? interpolateColorBasedOnScore(0, 1)
        : interpolateColorBasedOnScore(1, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.secondary",
          display: "flex",
          height: "100%",
          alignItems: "center",
        }}
      >
        {_.capitalize(value)}
        {compositeBadge}
        <RenderMeta
          originType={originType}
          meta={meta}
          showToken={!isFutureAgiEval}
        />
      </Box>
    );
  }
  if (dataType === "float") {
    const normalized = normalizeEvalResult(value, output);
    if (normalized.kind === "choices") {
      return (
        <Box
          sx={{
            p: 1,
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
            overflow: "auto",
            height: "100%",
            alignItems: "flex-start",
            alignContent: "flex-start",
          }}
        >
          {normalized?.items?.map((item) => (
            <Chip
              key={item}
              label={item}
              size="small"
              variant="outlined"
              sx={{
                borderRadius: "4px",
                borderColor: "purple.500",
                color: "purple.500",
                fontWeight: 400,
                typography: "s3",
              }}
            />
          ))}
        </Box>
      );
    }
    const parsedValue = Number(value);
    const numericValue = Number.isFinite(parsedValue)
      ? parsedValue
      : normalized.score;
    const hasValue = Number.isFinite(numericValue);
    const bgColor = hasValue
      ? interpolateColorBasedOnScore(numericValue, 1)
      : "";
    return (
      <Box
        sx={{
          padding: 1,
          backgroundColor: bgColor,
          color: "text.primary",
          display: "flex",
          height: "100%",
          alignItems: "center",
        }}
      >
        {hasValue ? `${getScorePercentage(numericValue)}%` : ""}
        {compositeBadge}
        <RenderMeta
          originType={originType}
          meta={meta}
          showToken={!isFutureAgiEval}
        />
      </Box>
    );
  }

  if (dataType === "array") {
    return (
      <EvaluateArrayCellRenderer
        meta={meta}
        isFutureAgiEval={isFutureAgiEval}
        value={value}
        choicesMap={choicesMap}
      />
    );
  }

  return (
    <Box
      sx={{
        padding: "4px 8px",
        whiteSpace: "pre-wrap",
        lineHeight: "1.5",
        overflow: "hidden",
        textOverflow: "ellipsis",
        display: "-webkit-box",
        WebkitLineClamp: "6",
        WebkitBoxOrient: "vertical",
      }}
    >
      {value}
      {compositeBadge}
      <RenderMeta
        originType={originType}
        meta={meta}
        showToken={!isFutureAgiEval}
      />
    </Box>
  );
};

EvaluateCell.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  cellData: PropTypes.object,
  originType: PropTypes.string,
  choicesMap: PropTypes.object,
  outputType: PropTypes.string,
};

export default EvaluateCell;
