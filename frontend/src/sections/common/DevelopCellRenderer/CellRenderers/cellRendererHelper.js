import PropTypes from "prop-types";

const collapsedHeight = "150px";
const expandedHeight = "300px";

export const collapsedStyles = {
  maxHeight: collapsedHeight,
  overflow: "hidden",
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
  // CustomCellRender passes value_infos.reason as a string (toString()).
  valueReason: PropTypes.oneOfType([PropTypes.string, PropTypes.array]),
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
  // Round 2 (R2-SPEC P5 / P6): pass through the wire like `json`.
  CONVERSATION: "conversation",
  CHECKS: "checks",
  TRACE: "trace",
  ENUM: "enum",
  VIDEO: "video",
};

export const OutputTypes = {
  NUMERIC: "numeric",
  SCORE: "score",
};

export const StatusTypes = {
  RUNNING: "running",
  ERROR: "error",
};

// Column-level statuses meaning the column is still computing. Shared with the
// grid polling gate in DevelopDataV2.
export const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
];

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
