import React from "react";
import { Box, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import CellRunButton from "src/components/CellActionButton/CellRunButton";
import { OriginTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { CELL_STATE } from "./common";
import EvaluationCellRenderer from "../EvaluationCellRenderer";
import GridIcon from "src/components/gridIcon/GridIcon";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";
import TestAudioPlayer from "src/components/custom-audio/TestAudioPlayer";
import PromptLoading from "../Playground/OutputSection/PromptLoading/PromptLoading";

const ImageCell = ({ value, onRun }) => {
  const { setImageUrl } = useSingleImageViewContext();
  const isEmpty = !value || value === CELL_STATE.EMPTY;
  const isLoading = value === CELL_STATE.LOADING;

  return (
    <CellRunButton
      show
      component={
        <IconButton sx={{ padding: 0 }}>
          <img src={"/assets/icons/ic_run.svg"} />
        </IconButton>
      }
      onClick={onRun}
    >
      <Box
        sx={{
          display: "flex",
          height: "100%",
          justifyContent: "flex-start",
          padding: "4px 8px",
        }}
      >
        {isLoading ? (
          <PromptLoading />
        ) : isEmpty ? (
          <Box
            display="flex"
            alignItems="center"
            bgcolor="status.info.bg"
            width="100%"
            height="100%"
          >
            <Typography display="flex" flexDirection="row" alignItems="center">
              NA
              <Typography color="status.info.fg">
                (Hover and click <img src={"/assets/icons/ic_run.svg"} /> to
                run)
              </Typography>
            </Typography>
          </Box>
        ) : (
          <GridIcon
            height="100%"
            src={value}
            alt=""
            onClick={(e) => {
              e.stopPropagation();
              setImageUrl?.(value);
            }}
            sx={{
              cursor: "pointer",
              borderRadius: "8px",
              maxWidth: "180px",
              width: "100%",
              height: "100%",
              objectFit: "cover",
            }}
          />
        )}
      </Box>
    </CellRunButton>
  );
};

ImageCell.propTypes = {
  value: PropTypes.string,
  onRun: PropTypes.func,
};

const AudioCell = ({ value, onRun, rowIndex }) => {
  const isEmpty = !value || value === CELL_STATE.EMPTY;
  const isLoading = value === CELL_STATE.LOADING;

  return (
    <CellRunButton
      show
      component={
        <IconButton sx={{ padding: 0 }}>
          <img src={"/assets/icons/ic_run.svg"} />
        </IconButton>
      }
      onClick={onRun}
    >
      <Box
        sx={{
          display: "flex",
          height: "100%",
          alignItems: "center",
          padding: "4px 8px",
          width: "100%",
        }}
      >
        {isLoading ? (
          <PromptLoading />
        ) : isEmpty ? (
          <Box
            display="flex"
            alignItems="center"
            bgcolor="status.info.bg"
            width="100%"
            height="100%"
          >
            <Typography display="flex" flexDirection="row" alignItems="center">
              NA
              <Typography color="status.info.fg">
                (Hover and click <img src={"/assets/icons/ic_run.svg"} /> to
                run)
              </Typography>
            </Typography>
          </Box>
        ) : (
          <Box sx={{ width: "100%" }}>
            <TestAudioPlayer
              audioData={{ url: value }}
              cacheKey={`eval-audio-${rowIndex}-${value}`}
            />
          </Box>
        )}
      </Box>
    </CellRunButton>
  );
};

AudioCell.propTypes = {
  value: PropTypes.string,
  onRun: PropTypes.func,
  rowIndex: PropTypes.number,
};

const EvaluationCellRendererWrapper = (params) => {
  const { column, node } = params;
  const cellParams = column?.colDef?.cellRendererParams;
  const modelDetail = cellParams?.col?.model_detail;
  const modelType = modelDetail?.type;

  const originType = column?.colDef?.originType;
  const headerParams = column?.colDef?.headerComponentParams;
  const templateVersion = headerParams?.col?.template_version;
  const handleRun = cellParams?.col?.handleClick;

  const handleCellClick = () => {
    handleRun?.(originType, node.rowIndex, templateVersion);
  };

  if (originType === OriginTypes.RUN_PROMPT) {
    if (modelType === "image_generation") {
      return <ImageCell value={params.value} onRun={handleCellClick} />;
    }
    if (modelType === "tts") {
      return (
        <AudioCell
          value={params.value}
          onRun={handleCellClick}
          rowIndex={node.rowIndex}
        />
      );
    }
  }

  return <EvaluationCellRenderer {...params} />;
};

export default EvaluationCellRendererWrapper;
