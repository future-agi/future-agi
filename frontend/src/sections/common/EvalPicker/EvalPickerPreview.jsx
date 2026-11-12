import {
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import EvalTypeBadge from "src/sections/evals/components/EvalTypeBadge";
import TypeBadge from "src/sections/evals/components/TypeBadge";
import VersionBadge from "src/sections/evals/components/VersionBadge";
import { normalizeEvalPickerEval } from "./evalPickerValue";

const OUTPUT_TYPE_LABELS = {
  pass_fail: "Pass/fail",
  percentage: "Percentage",
  deterministic: "Choices",
};

const InfoRow = ({ label, children }) => (
  <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, py: 0.75 }}>
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ minWidth: 100, flexShrink: 0, pt: 0.25 }}
    >
      {label}
    </Typography>
    <Box sx={{ flex: 1 }}>{children}</Box>
  </Box>
);

InfoRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

const EvalPickerPreview = ({ evalData, onBack, onAddEval }) => {
  if (!evalData) return null;

  const normalizedEvalData = normalizeEvalPickerEval(evalData);

  const {
    name,
    description,
    evalType,
    templateType,
    outputType,
    currentVersion,
    instructions,
    model,
    requiredKeys,
  } = normalizedEvalData;

  const variables = requiredKeys || [];

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          pb: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <IconButton size="small" onClick={onBack} sx={{ p: 0.5 }}>
            <Iconify icon="solar:arrow-left-linear" width={20} />
          </IconButton>
          <Typography variant="subtitle1" fontWeight={600} noWrap>
            {name}
          </Typography>
          {currentVersion && <VersionBadge version={currentVersion} />}
        </Box>
      </Box>

      <Divider />

      {/* Content */}
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          py: 2,
          display: "flex",
          flexDirection: "column",
          gap: 0.5,
        }}
      >
        <InfoRow label="Type">
          <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
            <TypeBadge type={templateType} />
            <EvalTypeBadge type={evalType} />
          </Box>
        </InfoRow>

        <InfoRow label="Output Type">
          <Typography variant="body2" sx={{ fontSize: "13px" }}>
            {OUTPUT_TYPE_LABELS[outputType] || outputType || "—"}
          </Typography>
        </InfoRow>

        {model && (
          <InfoRow label="Model">
            <Chip
              label={model}
              size="small"
              variant="outlined"
              sx={{
                borderColor: "divider",
              }}
            />
          </InfoRow>
        )}

        {description && (
          <InfoRow label="Description">
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ fontSize: "13px" }}
            >
              {description}
            </Typography>
          </InfoRow>
        )}

        {variables.length > 0 && (
          <InfoRow label="Variables">
            <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
              {variables.map((v) => (
                <Chip
                  key={v}
                  label={v}
                  size="small"
                  sx={{
                    bgcolor: "action.hover",
                    color: "text.secondary",
                    "& .MuiChip-label": { px: 0.75 },
                  }}
                />
              ))}
            </Box>
          </InfoRow>
        )}

        {instructions && (
          <>
            <Divider sx={{ my: 1 }} />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Instructions Preview
            </Typography>
            <Box
              sx={{
                p: 1.5,
                borderRadius: 1,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                maxHeight: 200,
                overflow: "auto",
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  fontSize: "12px",
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {instructions}
              </Typography>
            </Box>
          </>
        )}
      </Box>

      {/* Actions */}
      <Divider />
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 1,
          pt: 2,
        }}
      >
        <Button
          variant="outlined"
          size="small"
          onClick={onBack}
          sx={{ minWidth: 80 }}
        >
          Back
        </Button>
        <Button
          variant="contained"
          size="small"
          onClick={() => onAddEval(evalData)}
          startIcon={<Iconify icon="solar:add-linear" width={16} />}
          sx={{ minWidth: 120 }}
        >
          Add Evaluation
        </Button>
      </Box>
    </Box>
  );
};

EvalPickerPreview.propTypes = {
  evalData: PropTypes.object,
  onBack: PropTypes.func.isRequired,
  onAddEval: PropTypes.func.isRequired,
};

export default EvalPickerPreview;
