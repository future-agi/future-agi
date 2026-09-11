import { Box, Button, IconButton, Typography } from "@mui/material";
import _ from "lodash";
import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import CellRunButton from "src/components/CellActionButton/CellRunButton";
import {
  OriginTypes,
  StatusTypes,
} from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import EvaluateCellRendererWrapper from "src/sections/common/DevelopCellRenderer/CellRenderers/EvaluateCellRendererWrapper";
import RunningSkeletonRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer";
import PromptLoading from "./Playground/OutputSection/PromptLoading/PromptLoading";
import ErrorCellRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/ErrorCellRenderer";
import { dataTypeMapping } from "./common";
import { parseThinkingContent } from "./Playground/OutputSection/thinkingUtils";
import ThinkingBlock from "./Playground/OutputSection/ThinkingBlock";
import { CELL_STATE } from "./Evaluation/common";
import { useWorkbenchEvaluationContext } from "./Evaluation/context/WorkbenchEvaluationContext";

const FormattedReason = ({ title }) => {
  const contentRef = useRef(null);
  const [expanded, setExpanded] = useState(false);
  const [isScrollable, setIsScrollable] = useState(false);
  const [hasScrolled, setHasScrolled] = useState(false);
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
  }, [expanded, title]);

  useEffect(() => {
    if (!expanded) {
      setHasScrolled(false);
    }
  }, [expanded]);
  const contentLength = title.length;

  return (
    <Box
      ref={contentRef}
      sx={{
        position: "relative",
      }}
      onScroll={expanded ? handleScroll : undefined}
    >
      <Box
        sx={{
          minWidth: "min-content",
        }}
      >
        <Box
          sx={{
            "& pre": {
              whiteSpace: "pre-wrap",
            },
            wordBreak: "break-all",
          }}
        >
          <Markdown>
            {contentLength > 200 && !expanded
              ? `${title.slice(0, 200)}...`
              : title}
          </Markdown>
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
                  `linear-gradient(355.88deg, ${theme.palette.background.paper} 3.69%, ${theme.palette.background.paper}7f 50.34%, ${theme.palette.background.paper}19 96.99%)`,
                pointerEvents: "none",
              }}
            />
          )}
        </Box>
      </Box>
      {title.length > 200 && (
        <Button
          onClick={() => setExpanded(!expanded)}
          variant="text"
          disableRipple
          sx={{
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
            {expanded ? "Show less" : "Show more"}
          </Typography>
        </Button>
      )}
    </Box>
  );
};

FormattedReason.propTypes = {
  title: PropTypes.string,
};

const EvaluationCellRenderer = (params) => {
  const {
    value,
    column,
    colDef,
    node: { rowIndex },
  } = params;
  // Normalize non-object values to strings for Markdown compatibility
  const displayValue =
    value != null && typeof value !== "object" ? String(value) : value;
  const isNA = value === "";
  const isOutputEmpty = value === CELL_STATE.EMPTY;
  const { meta, value: cellValue, status, output } = value ?? {};
  const isCellValueNA =
    cellValue === null || cellValue === undefined || cellValue === "";
  const cellParams = column?.colDef?.cellRendererParams;
  const headerParams = column?.colDef?.headerComponentParams;

  const dataType = dataTypeMapping[output] ?? "text";
  const originType = column?.colDef?.originType;
  const handleRun = cellParams?.col?.handleClick;
  // Hide the hover rerun button while the Evaluation drawer is open —
  // the button's tooltip renders via a portal at z-index 1500 which
  // otherwise overlays the drawer (see EvaluationDrawer Paper zIndex).
  const { isEvaluationDrawerOpen } = useWorkbenchEvaluationContext();
  const showRunButton = !isEvaluationDrawerOpen;
  const templateVersion = headerParams?.col?.template_version;
  const evalTemplateId = cellParams?.col?.evalTemplateId;
  const choicesMap = colDef?.headerComponentParams?.col?.choices_map ?? {};

  if (status === "Running") {
    return <RunningSkeletonRenderer originType={originType} />;
  }

  const shouldBeStringified = ["array", "float"].includes(dataType);

  const handleCellClick = (originType) => {
    // Live index at click time — the destructured rowIndex goes stale after a
    // row delete/reorder, which sent the run to the wrong position.
    const currentRowIndex = params.node?.rowIndex ?? rowIndex;
    switch (originType) {
      case OriginTypes.EVALUATION:
        handleRun(originType, currentRowIndex, templateVersion, evalTemplateId);
        break;
      default:
        handleRun(originType, currentRowIndex, templateVersion);
        break;
    }
  };

  if (status?.toLowerCase() === StatusTypes.ERROR) {
    return (
      <CellRunButton
        show={showRunButton}
        component={
          <IconButton sx={{ padding: 0 }}>
            <img src={"/assets/icons/ic_run.svg"} />
          </IconButton>
        }
        onClick={() => handleCellClick(originType)}
      >
        <Box display={"flex"} flex={1}>
          <ErrorCellRenderer
            formattedValueReason={() => (
              <FormattedReason title={meta?.reason} />
            )}
            valueReason={meta?.reason}
          />
        </Box>
      </CellRunButton>
    );
  }

  switch (originType) {
    case OriginTypes.EVALUATION:
      return (
        <CellRunButton
          show={showRunButton}
          component={
            <IconButton sx={{ padding: 0 }}>
              <img src={"/assets/icons/ic_run.svg"} />
            </IconButton>
          }
          onClick={() => handleCellClick(originType)}
        >
          <Box height={"100%"}>
            {!isCellValueNA ? (
              <EvaluateCellRendererWrapper
                formattedValueReason={() => (
                  <FormattedReason title={meta?.reason} />
                )}
                valueReason={meta?.reason}
                cellData={{
                  cellValue: shouldBeStringified
                    ? JSON.stringify(cellValue)
                    : cellValue,
                }}
                originType={OriginTypes.EVALUATION}
                choicesMap={choicesMap}
                dataType={dataType}
                value={
                  shouldBeStringified ? JSON.stringify(cellValue) : cellValue
                }
              />
            ) : (
              <Box
                px={1}
                overflow={"auto"}
                bgcolor={"background.default"}
                height={"100%"}
                display={"flex"}
                whiteSpace={"wrap"}
                lineHeight={2}
                alignItems={"center"}
              >
                <Markdown>{"NA"}</Markdown>
              </Box>
            )}
          </Box>
        </CellRunButton>
      );
    case OriginTypes.RUN_PROMPT: {
      const parsedOutput =
        value && typeof value === "string" && value.trim()
          ? parseThinkingContent(value)
          : { thinking: "", content: value || "", isThinking: false };
      return (
        <CellRunButton
          show={showRunButton}
          component={
            <IconButton sx={{ padding: 0 }}>
              <img src={"/assets/icons/ic_run.svg"} />
            </IconButton>
          }
          onClick={() => handleCellClick(originType)}
        >
          <Box
            p={1}
            overflow={"auto"}
            bgcolor={isOutputEmpty && "blue.o10"}
            height={"100%"}
            display={isOutputEmpty && "flex"}
            whiteSpace={"wrap"}
            lineHeight={2}
            alignItems={"center"}
          >
            {isOutputEmpty ? (
              <Typography
                display={"flex"}
                flexDirection={"row"}
                alignItems={"center"}
              >
                NA
                <Typography color={"blue.500"}>
                  (Hover and click <img src={"/assets/icons/ic_run.svg"} /> to
                  run)
                </Typography>
              </Typography>
            ) : value === CELL_STATE.LOADING ? (
              <PromptLoading />
            ) : (
              <>
                {parsedOutput.thinking && (
                  <ThinkingBlock
                    content={parsedOutput.thinking}
                    isThinking={parsedOutput.isThinking ?? false}
                    outputType="markdown"
                  />
                )}
                <Markdown>{String(parsedOutput.content ?? "")}</Markdown>
              </>
            )}
          </Box>
        </CellRunButton>
      );
    }
    default:
      return (
        <Box
          px={1}
          overflow={"auto"}
          bgcolor={value === "" && "background.default"}
          height={"100%"}
          display={isNA && "flex"}
          whiteSpace={"wrap"}
          lineHeight={2}
          alignItems={"center"}
        >
          <Markdown>{isNA ? "NA" : displayValue}</Markdown>
        </Box>
      );
  }
};

EvaluationCellRenderer.propTypes = {
  params: PropTypes.object,
};

export default EvaluationCellRenderer;
