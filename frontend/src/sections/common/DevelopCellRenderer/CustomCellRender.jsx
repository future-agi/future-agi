import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import FloatIntegerCellRenderer from "./CellRenderers/FloatIntegerCellRenderer";
import DatetimeCellRenderer from "./CellRenderers/DatetimeCellRenderer";
import TextArrayJsonCellRenderer from "./CellRenderers/TextArrayJsonCellRenderer";
import ImageCellRenderer from "./CellRenderers/ImageCellRenderer";
import ImagesCellRenderer from "./CellRenderers/ImagesCellRenderer";
import AudioCellRenderer from "./CellRenderers/AudioCellRenderer";
import RunningSkeletonRenderer from "./CellRenderers/RunningSkeletonRenderer";
import ErrorCellRenderer from "./CellRenderers/ErrorCellRenderer";
import EvaluateCellRendererWrapper from "./CellRenderers/EvaluateCellRendererWrapper";
import AnnotationArrayCellRenderer from "./CellRenderers/AnnotationArrayCellRenderer";
import CellMarkdown from "../CellMarkdown";
import GenerateDiffText from "../GenerateDiffText";
import {
  buttonSx,
  collapsedStyles,
  DataTypes,
  expandedStyles,
  OriginTypes,
  StatusTypes,
  OutputTypes,
} from "./CellRenderers/cellRendererHelper";
import { useRowHover } from "src/hooks/use-row-hover";
import { useQueryClient } from "@tanstack/react-query";
import JsonCellRenderer from "./CellRenderers/JsonCellRenderer";
import FileCellRenderer from "./CellRenderers/FileCellRenderer";
import PersonaCellRenderer from "./CellRenderers/PersonaCellRenderer";
import { ORIGIN_OF_COLUMNS } from "src/utils/constants";
import EvaluationReasonFallback from "../EvaluationReasonFallback";
import { ShowComponent } from "src/components/show";

const CustomCellRender = (props) => {
  const hoverButtonVisible = props?.column?.colDef?.col?.isHoverButtonVisible;
  const dataType = props?.column?.colDef?.dataType;
  const originType = props?.column?.colDef?.originType;
  const value = props?.value;
  const valueInfos =
    props?.data?.[props?.column?.colId]?.value_infos ||
    props?.data?.[props?.column?.colId];
  const cellData = props?.data?.[props?.column?.colId];
  const status = cellData?.status?.toLowerCase();
  const valueReason = cellData?.value_infos?.reason?.toString();
  const output =
    cellData?.value_infos?.output || props?.column?.colDef?.col?.output_type;
  const column = props?.column?.colDef?.col;
  const evalTag = column?.eval_tag || [];
  const isFutureAgiEval = evalTag.includes("futureagi");

  // Detect code-eval columns so we can hide "Add feedback" — code evals
  // don't expose a feedback surface. We look up the eval_type via the
  // cached saved-evals list (queryKey set in getEvalsList.jsx). The
  // cache stores the raw axios response (not the `select`-transformed
  // data), so we have to dig through `data.data.result.evals`. This is
  // a synchronous read from the react-query cache so it adds no network
  // cost when the cache is already populated by EvaluationDrawer or
  // DatapointDrawerV2.
  const queryClient = useQueryClient();
  const isCodeEvalColumn = (() => {
    const sourceId = column?.sourceId || column?.source_id;
    if (!sourceId) return false;
    const queries = queryClient.getQueriesData({
      queryKey: ["develop", "user-eval-list"],
    });
    for (const [, raw] of queries) {
      // Support both shapes: raw axios response and select-transformed.
      const evals =
        raw?.data?.result?.evals || raw?.result?.evals || raw?.evals || [];
      const match = evals.find(
        (e) =>
          e.id === sourceId ||
          e.user_eval_id === sourceId ||
          e.userEvalId === sourceId,
      );
      if (match) {
        return (match.eval_type || match.evalType) === "code";
      }
    }
    return false;
  })();
  const { setImageUrl } = useSingleImageViewContext();
  const rowId = props?.data?.row_id;
  const choicesMap = props?.column?.colDef?.col?.choices_map;
  const isValueTypeArray = Array.isArray(value);
  const [expanded, setExpanded] = useState(false);
  const [hasScrolled, setHasScrolled] = useState(false);
  const contentRef = useRef(null);
  const [isScrollable, setIsScrollable] = useState(false);

  const { isRowHovered, cellRef } = useRowHover(props);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleScroll = () => {
    if (!contentRef.current || hasScrolled) return;

    const { scrollTop } = contentRef.current;
    if (scrollTop > 8) {
      setHasScrolled(true);
    }
  };

  useEffect(() => {
    const checkScrollability = () => {
      if (contentRef.current) {
        const isContentTaller =
          contentRef.current.scrollHeight > contentRef.current.clientHeight;
        setIsScrollable(isContentTaller);
      }
    };

    checkScrollability();
  }, [expanded, valueReason]);

  useEffect(() => {
    if (!expanded) {
      setHasScrolled(false);
    }
  }, [expanded]);

  const formattedValueReason = useCallback(() => {
    const valueReasonLength = valueReason && valueReason?.length;

    return (
      <Box
        sx={{
          maxWidth: "100%",
          p: "4px 0px 4px 4px",
          overflow: "hidden",
        }}
      >
        <Box
          ref={contentRef}
          className="promptScroll"
          sx={{
            position: "relative",
            ...(expanded ? expandedStyles : collapsedStyles),
          }}
          onScroll={expanded ? handleScroll : undefined}
        >
          <Box
            sx={{
              overflow: "hidden",
              "& pre": {
                whiteSpace: "pre-wrap",
              },
              wordBreak: "break-all",
            }}
          >
            {isValueTypeArray ? (
              <GenerateDiffText cellText={value} />
            ) : (
              <CellMarkdown fontSize={12} text={valueReason} />
            )}
          </Box>
          {expanded && isScrollable && !hasScrolled && (
            <Box
              sx={{
                position: "absolute",
                bottom: 0,
                left: 0,
                right: 0,
                height: "64px",
                opacity: "0.9",
                // background: `linear-gradient(to bottom, ${theme.palette.background.default} 10%, ${theme.palette.background.default} 50%, ${theme.palette.background.default})`,
                background: (theme) =>
                  `linear-gradient(355.88deg, ${theme.palette.background.default} 3.69%, ${theme.palette.background.default}7f 50.34%, ${theme.palette.background.default}19 96.99%)`,
                pointerEvents: "none",
              }}
            />
          )}
        </Box>

        {valueReasonLength > 300 && !expanded && (
          <Button
            onClick={() => setExpanded(!expanded)}
            sx={{
              textTransform: "none",
              p: 0,
              mt: 0.5,
              minWidth: "auto",
              color: "text.primary",
              textDecoration: "underline",
              fontSize: "14px",
              "&:hover": {
                backgroundColor: "transparent",
              },
            }}
          >
            <Typography typography="s2" fontWeight={"fontWeightSemiBold"}>
              {expanded ? "" : "Show more"}
            </Typography>
          </Button>
        )}
        <ShowComponent condition={output !== OutputTypes.NUMERIC}>
          {hoverButtonVisible &&
            (originType === OriginTypes.EVALUATION ||
              originType === OriginTypes.OPTIMISATION_EVALUATION) &&
            status !== "error" && (
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  mt: 2,
                  gap: 1,
                }}
              >
                {originType !== OriginTypes.RUN_PROMPT && !isCodeEvalColumn && (
                  <Button
                    variant="outlined"
                    sx={buttonSx}
                    onClick={() => {
                      column.feedBackClick({
                        ...cellData,
                        ...column,
                        rowData: props?.data,
                      });
                    }}
                  >
                    Add feedback
                  </Button>
                )}
                {column?.metadata?.runPrompt && (
                  <Button
                    variant="outlined"
                    sx={buttonSx}
                    onClick={() => {
                      column.improvementClick({
                        ...cellData,
                        ...column,
                        rowData: props?.data,
                      });
                    }}
                  >
                    Improve prompt
                  </Button>
                )}
              </Box>
            )}
        </ShowComponent>
      </Box>
    );
  }, [
    cellData,
    column,
    expanded,
    handleScroll,
    hasScrolled,
    hoverButtonVisible,
    isScrollable,
    isValueTypeArray,
    originType,
    output,
    isCodeEvalColumn,
    props?.data,
    status,
    value,
    valueReason,
  ]);

  if (props?.node?.rowPinned === "bottom") {
    return <Box sx={{ padding: 1, lineHeight: 1.5 }}>{cellData}</Box>;
  }

  if (status === StatusTypes.ERROR) {
    if (originType === "evaluation_reason") {
      return <EvaluationReasonFallback message={value} />;
    }
    return (
      <ErrorCellRenderer
        valueReason={valueReason}
        formattedValueReason={formattedValueReason}
        props={props}
        onRerun={props?.onRerun}
      />
    );
  }

  if (status === StatusTypes.RUNNING || value === undefined) {
    return (
      <RunningSkeletonRenderer
        originType={props.colDef.col.originType}
        originOfColumn={props?.originOfColumn === ORIGIN_OF_COLUMNS.EXPERIMENT}
      />
    );
  }
  if (dataType === DataTypes.AUDIO) {
    const cacheKey = `wavesurfer-${rowId}-${column.id}-${value}`;
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexGrow: 1,
        }}
      >
        <AudioCellRenderer
          value={value}
          editable={props?.editable}
          cacheKey={cacheKey}
          getWaveSurferInstance={column?.getWaveSurferInstance}
          storeWaveSurferInstance={column?.storeWaveSurferInstance}
          updateWaveSurferInstance={column?.updateWaveSurferInstance}
          onEditCell={() =>
            props.colDef?.cellRendererParams?.onEditCell?.(props)
          }
          onCellValueChanged={
            props.colDef?.cellRendererParams?.onCellValueChanged
          }
          params={props}
          page={column?.page}
        />
      </Box>
    );
  }

  if (
    originType === OriginTypes.EVALUATION ||
    originType === OriginTypes.OPTIMISATION_EVALUATION
  ) {
    return (
      <EvaluateCellRendererWrapper
        valueReason={valueReason}
        formattedValueReason={formattedValueReason}
        choicesMap={choicesMap}
        cellData={cellData}
        value={value}
        dataType={dataType}
        originType={originType}
        isFutureAgiEval={isFutureAgiEval}
        outputType={output}
      />
    );
  }

  if (originType === OriginTypes.ANNOTATION_LABEL) {
    if (dataType === DataTypes.ARRAY) {
      return <AnnotationArrayCellRenderer value={value} cellData={cellData} />;
    }
    if (typeof value === "string" && value.trim().startsWith("{")) {
      let parsedEnvelope = null;
      try {
        parsedEnvelope = JSON.parse(value.replaceAll("'", '"'));
      } catch {
        parsedEnvelope = null;
      }
      if (
        parsedEnvelope &&
        typeof parsedEnvelope === "object" &&
        ("selected" in parsedEnvelope ||
          "rating" in parsedEnvelope ||
          "value" in parsedEnvelope ||
          "text" in parsedEnvelope)
      ) {
        return (
          <AnnotationArrayCellRenderer value={value} cellData={cellData} />
        );
      }
    }
  }

  switch (dataType) {
    case DataTypes.FLOAT:
    case DataTypes.INTEGER:
      return (
        <FloatIntegerCellRenderer
          value={value}
          valueReason={valueReason}
          formattedValueReason={formattedValueReason}
          originType={originType}
          metadata={cellData?.metadata}
          valueInfos={valueInfos}
        />
      );

    case DataTypes.DATETIME:
      return (
        <DatetimeCellRenderer
          value={value}
          valueReason={valueReason}
          formattedValueReason={formattedValueReason}
          originType={originType}
          metadata={cellData?.metadata}
        />
      );
    case DataTypes.JSON:
      return (
        <Box ref={cellRef} sx={{ height: "100%" }}>
          <JsonCellRenderer
            isHover={isRowHovered}
            value={value}
            valueReason={valueReason}
            formattedValueReason={formattedValueReason}
            originType={originType}
            metadata={cellData?.metadata}
            valueInfos={valueInfos}
          />
        </Box>
      );
    case DataTypes.TEXT:
    case DataTypes.ARRAY:
    case DataTypes.BOOLEAN:
      return (
        <Box ref={cellRef} sx={{ height: "100%" }}>
          <TextArrayJsonCellRenderer
            isHover={isRowHovered}
            value={value}
            valueReason={valueReason}
            formattedValueReason={formattedValueReason}
            originType={originType}
            metadata={cellData?.metadata}
            valueInfos={valueInfos}
            cellData={cellData}
          />
        </Box>
      );

    case DataTypes.IMAGE:
      return (
        <Box ref={cellRef} sx={{ height: "100%" }}>
          <ImageCellRenderer
            value={value}
            editable={props?.editable}
            valueReason={valueReason}
            formattedValueReason={formattedValueReason}
            originType={originType}
            metadata={cellData?.metadata}
            setImageUrl={setImageUrl}
            onEditCell={() =>
              props.colDef?.cellRendererParams?.onEditCell?.(props)
            }
            params={props}
            valueInfos={valueInfos}
            isHover={isRowHovered}
            onCellValueChanged={
              props.colDef?.cellRendererParams?.onCellValueChanged
            }
          />
        </Box>
      );

    case DataTypes.IMAGES:
      return (
        <ImagesCellRenderer
          value={value}
          editable={props?.editable}
          valueReason={valueReason}
          formattedValueReason={formattedValueReason}
          originType={originType}
          metadata={cellData?.metadata}
          onEditCell={() =>
            props.colDef?.cellRendererParams?.onEditCell?.(props)
          }
          params={props}
          onCellValueChanged={
            props.colDef?.cellRendererParams?.onCellValueChanged
          }
        />
      );

    case DataTypes.FILE:
      return (
        <FileCellRenderer
          value={value}
          editable={props?.editable}
          valueReason={valueReason}
          formattedValueReason={formattedValueReason}
          originType={originType}
          metadata={cellData?.metadata}
          setImageUrl={setImageUrl}
          onEditCell={(extra) =>
            props.colDef?.cellRendererParams?.onEditCell?.({
              ...props,
              ...extra,
            })
          }
          params={props}
          onCellValueChanged={
            props.colDef?.cellRendererParams?.onCellValueChanged
          }
          valueInfos={valueInfos}
        />
      );
    case DataTypes.PERSONA:
      return <PersonaCellRenderer value={value} />;
  }
};

CustomCellRender.propTypes = {
  column: PropTypes.object,
  value: PropTypes.any,
  data: PropTypes.object,
  node: PropTypes.any,
  colDef: PropTypes.any,
  editable: PropTypes.bool,
  originOfColumn: PropTypes.string,
  onRerun: PropTypes.func,
};

export default React.memo(CustomCellRender);
