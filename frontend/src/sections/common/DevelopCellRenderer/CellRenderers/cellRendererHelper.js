import PropTypes from "prop-types";

const collapsedHeight = "150px";
const expandedHeight = "300px";
const lineClamp = 6;

export const collapsedStyles = {
  maxHeight: collapsedHeight,
  display: "-webkit-box",
  WebkitLineClamp: lineClamp,
  WebkitBoxOrient: "vertical",
  overflowY: "hidden",
  textOverflow: "ellipsis",
};

export const expandedStyles = {
  maxHeight: expandedHeight,
  display: "block",
  WebkitLineClamp: "none",
  WebkitBoxOrient: "vertical",
  overflowY: "auto",
};

export const commonPropTypes = {
  value: PropTypes.any,
  valueReason: PropTypes.array,
  formattedValueReason: PropTypes.func.isRequired,
  originType: PropTypes.string,
  metadata: PropTypes.any,
  valueInfos: PropTypes.any,
  setImageUrl: PropTypes.func,
  editable: PropTypes.bool,
};

export const buttonSx = {
  minWidth: "max-content",
  width: "100%",
  fontSize: "12px",
  lineHeight: "18px",
  padding: "6px 24px",
  color: "primary.main",
  borderColor: "primary.main",
  borderRadius: "8px",
  textTransform: "none",
  fontWeight: 600,
  height: "30px",
  boxShadow: "none",
  "&:hover": {
    borderColor: "primary.main",
    backgroundColor: "action.hover",
  },
};

export const DataTypes = {
  AUDIO: "audio",
  BOOLEAN: "boolean",
  FLOAT: "float",
  INTEGER: "integer",
  DATETIME: "datetime",
  TEXT: "text",
  ARRAY: "array",
  JSON: "json",
  IMAGE: "image",
  IMAGES: "images",
  FILE: "document",
  PERSONA: "persona",
};

export const OutputTypes = {
  NUMERIC: "numeric",
  SCORE: "score",
};

export const StatusTypes = {
  RUNNING: "running",
  ERROR: "error",
};

export const OriginTypes = {
  EVALUATION: "evaluation",
  RUN_PROMPT: "run_prompt",
  OPTIMISATION_EVALUATION: "optimisation_evaluation",
  ANNOTATION_LABEL: "annotation_label",
  VARIABLE: "variable",
};

export const tooltipSlotProp = {
  popper: {
    sx: {
      pointerEvents: "auto", //ensures it does not interfere with mouse interactions on underlying elements
    },
    modifiers: [
      {
        name: "offset",
        options: {
          offset: [0, -20],
        },
      },
      {
        name: "flip",
        options: {
          fallbackPlacements: ["top", "bottom"],
          padding: 8,
        },
      },
    ],
  },
};
