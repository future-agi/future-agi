import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { typedEvalLabel } from "src/sections/common/EvalTypes/typedEvalTypes";

const EVAL_TYPE_CONFIG = {
  llm: {
    label: "LLM",
    icon: "mdi:gavel",
  },
  code: {
    label: "Code",
    icon: "solar:code-linear",
  },
  agent: {
    label: "Agent",
    icon: "solar:bot-linear",
  },
  // W7 typed types (typedEvalTypes.TYPED_EVAL_TYPES names them).
  dag: {
    label: typedEvalLabel("dag"),
    icon: "mdi:source-branch",
  },
  remote: {
    label: typedEvalLabel("remote"),
    icon: "mdi:web",
  },
};

const EvalTypeBadge = ({ type = "llm" }) => {
  // Composite evals: type can be comma-separated like "code, llm"
  const types = type
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

  if (types.length <= 1) {
    const config = EVAL_TYPE_CONFIG[types[0]] || EVAL_TYPE_CONFIG.llm;
    return (
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        <Iconify
          icon={config.icon}
          width={16}
          sx={{ color: "text.secondary" }}
        />
        <Typography variant="caption" color="text.secondary">
          {config.label}
        </Typography>
      </Box>
    );
  }

  // Multiple types: show each icon + joined label
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      {types.map((t) => {
        const config = EVAL_TYPE_CONFIG[t] || EVAL_TYPE_CONFIG.llm;
        return (
          <Iconify
            key={t}
            icon={config.icon}
            width={16}
            sx={{ color: "text.secondary" }}
          />
        );
      })}
      <Typography variant="caption" color="text.secondary">
        {types
          .map((t) => (EVAL_TYPE_CONFIG[t] || EVAL_TYPE_CONFIG.llm).label)
          .join(", ")}
      </Typography>
    </Box>
  );
};

EvalTypeBadge.propTypes = {
  type: PropTypes.string,
};

export default EvalTypeBadge;
