import {
  Alert,
  Box,
  Chip,
  Divider,
  LinearProgress,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";

function ProbabilityBar({ label, value }) {
  if (!Number.isFinite(value)) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Typography variant="caption" sx={{ flex: 1 }}>
        {label}
      </Typography>
      <LinearProgress
        variant="determinate"
        aria-label={label}
        value={value * 100}
        sx={{ flex: 2, height: 6, borderRadius: 1 }}
      />
      <Typography variant="caption">{value.toFixed(2)}</Typography>
    </Box>
  );
}
ProbabilityBar.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.number,
};

export default function JevResultDisplay({ result, jev }) {
  // A valid "fail" verdict is a successful evaluation. Only execution errors
  // suppress scores; the legacy `failure` flag also denotes a failed verdict.
  const error =
    result.error || jev.error || result.error_code || jev.error_code;
  const failed = Boolean(error) || ["error", "failed"].includes(result.status);
  const errorMessage =
    typeof error === "string"
      ? error
      : error?.message ||
        result.message ||
        "Jev evaluation failed. Nothing was scored.";
  const output = normalizeEvalCellValue(result.output);
  const choice =
    typeof output === "string"
      ? output
      : output?.choice ?? output?.label ?? result.data?.result;
  const choiceScore = output?.score ?? result.score;
  const distribution = jev.distribution || {};
  const legend = jev.legend || {};
  const distributionKeys = [
    ...new Set([...Object.keys(legend), ...Object.keys(distribution)]),
  ];

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      {failed ? (
        <>
          <Alert severity="error">{errorMessage}</Alert>
          <Typography variant="caption" color="text.secondary">
            No Turing fallback was applied
          </Typography>
        </>
      ) : (
        <>
          {jev.question_type === "noul" && (
            <>
              <Chip
                label={jev.verdict === "pass" ? "Pass" : "Fail"}
                color={jev.verdict === "pass" ? "success" : "error"}
                size="small"
                sx={{ alignSelf: "flex-start" }}
              />
              {Number.isFinite(jev.probability) && (
                <Typography variant="body2">
                  Probability: {jev.probability.toFixed(2)}
                </Typography>
              )}
              <ProbabilityBar label="Probability" value={jev.probability} />
              {Number.isFinite(jev.threshold) && (
                <Typography variant="caption">
                  Threshold: {jev.threshold.toFixed(2)}
                </Typography>
              )}
            </>
          )}
          {jev.question_type === "choice" && (
            <>
              <Typography variant="body2">Chosen label: {choice}</Typography>
              {Number.isFinite(choiceScore) && (
                <Typography variant="body2">
                  Score: {choiceScore.toFixed(2)}
                </Typography>
              )}
            </>
          )}
          {jev.question_type === "score" && (
            <>
              {Number.isFinite(jev.raw_score) && (
                <Typography variant="body2">
                  Expected level: {jev.raw_score.toFixed(2)}
                </Typography>
              )}
              {Number.isFinite(jev.normalized_score) && (
                <Typography variant="body2">
                  Normalized score: {jev.normalized_score.toFixed(2)}
                </Typography>
              )}
              <ProbabilityBar
                label="Normalized score"
                value={jev.normalized_score}
              />
            </>
          )}
          {distributionKeys.length > 0 && (
            <>
              <Typography variant="body2" fontWeight={600}>
                {jev.question_type === "score" ? "Legend" : "Distribution"}
              </Typography>
              {distributionKeys.map((key) => {
                const label =
                  legend[key] != null ? `${key} — ${legend[key]}` : key;
                return Number.isFinite(distribution[key]) ? (
                  <ProbabilityBar
                    key={key}
                    label={label}
                    value={distribution[key]}
                  />
                ) : (
                  <Typography key={key} variant="caption">
                    {label}
                  </Typography>
                );
              })}
            </>
          )}
          {jev.question_type !== "noul" && Number.isFinite(jev.confidence) && (
            <Typography variant="caption">
              Confidence: {jev.confidence.toFixed(2)} (provider-reported)
            </Typography>
          )}
        </>
      )}
      <Divider />
      <Typography variant="caption">
        Requested: {jev.requested_model || "—"}
      </Typography>
      <Typography variant="caption">
        Returned: {jev.actual_model || "—"}
      </Typography>
      {jev.mapping_revision && (
        <Typography variant="caption">
          Mapping: {jev.mapping_revision}
        </Typography>
      )}
      {jev.usage && (
        <Typography variant="caption">
          Usage: {jev.usage.input_tokens ?? 0} input /{" "}
          {jev.usage.output_tokens ?? 0} output tokens
        </Typography>
      )}
      <Typography variant="caption" color="text.secondary">
        Jev does not provide written reasoning.
      </Typography>
    </Box>
  );
}
JevResultDisplay.propTypes = {
  result: PropTypes.object.isRequired,
  jev: PropTypes.object.isRequired,
};
