/* eslint-disable react/prop-types */
import {
  Box,
  Chip,
  CircularProgress,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import CompositeResultView from "./CompositeResultView";
import { canonicalEntries } from "src/utils/utils";

/**
 * Shared eval result display component.
 * Handles all output types: choices, scoring, pass/fail.
 * Includes Formatted / JSON toggle.
 */
const EvalResultDisplay = ({ result }) => {
  const [viewMode, setViewMode] = useState("formatted");

  // Support multiple shapes:
  //   LLM/Agent evals:  { output, outputType, reason, ... }
  //   Code evals:       { score, reason, metadata, ... }  — no "output" field
  //   Composite evals:  { compositeResult: { aggregate_score, children, ... } }
  if (!result) return null;

  // Short-circuit for composite results — render a dedicated view.
  if (result.compositeResult) {
    return <CompositeResultView compositeResult={result.compositeResult} />;
  }

  const hasLegacyOutput = result.output != null;
  const hasCodeScore =
    typeof result.score === "number" || typeof result.score === "boolean";
  if (!hasLegacyOutput && !hasCodeScore && !result.reason) return null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {/* View toggle */}
      <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
        <Box
          sx={{
            display: "inline-flex",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            overflow: "hidden",
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.04)"
                : "background.neutral",
          }}
        >
          {["formatted", "json"].map((mode) => (
            <Box
              key={mode}
              onClick={() => setViewMode(mode)}
              sx={{
                px: 1.5,
                py: 0.5,
                cursor: "pointer",
                fontSize: "11px",
                fontWeight: viewMode === mode ? 600 : 400,
                color: viewMode === mode ? "text.primary" : "text.disabled",
                backgroundColor:
                  viewMode === mode
                    ? (t) =>
                        t.palette.mode === "dark"
                          ? "rgba(255,255,255,0.12)"
                          : "background.paper"
                    : "transparent",
                transition: "all 0.15s",
                userSelect: "none",
              }}
            >
              {mode === "formatted" ? "Formatted" : "JSON"}
            </Box>
          ))}
        </Box>
      </Box>

      {viewMode === "json" ? (
        <JsonView data={result} />
      ) : (
        <FormattedResult result={result} />
      )}
    </Box>
  );
};

const FormattedResult = ({ result }) => {
  // For code evals, result.output is missing — use result.score as the raw value
  const raw = result.output != null ? result.output : result.score;
  const outputType = result.output_type;

  // ── Choices: output is {label, score, category} or [{...}, ...] ──
  const isChoiceObj =
    raw && typeof raw === "object" && !Array.isArray(raw) && raw.label;
  const isChoiceArr = Array.isArray(raw) && raw.length > 0 && raw[0]?.label;
  // Bare string/array choices (backend doesn't always wrap the label into
  // an object). Match the EvalCellRenderer look: outlined primary chips.
  const isBareChoice =
    (outputType === "choices" || outputType === "choice") &&
    !isChoiceObj &&
    !isChoiceArr &&
    (typeof raw === "string" ||
      (Array.isArray(raw) && raw.every((r) => typeof r === "string")));

  const renderResult = () => {
    if (isChoiceObj || isChoiceArr || isBareChoice) {
      const items = isBareChoice
        ? (Array.isArray(raw) ? raw : [raw]).map((label) => ({ label }))
        : isChoiceArr
          ? raw
          : [raw];
      return (
        <Box
          sx={{
            display: "flex",
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            alignItems: "center",
          }}
        >
          <Typography variant="caption" sx={{ flex: 1 }}>
            Result
          </Typography>
          <Box
            sx={{
              flex: 2,
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              flexWrap: "wrap",
            }}
          >
            {items.map((c, i) => {
              const label =
                typeof c.label === "string"
                  ? c.label.charAt(0).toUpperCase() + c.label.slice(1)
                  : String(c.label);
              const useCategoryColor =
                c.category === "pass" || c.category === "fail";
              return (
                <Chip
                  key={i}
                  label={label}
                  size="small"
                  variant={useCategoryColor ? "filled" : "outlined"}
                  color={
                    c.category === "pass"
                      ? "success"
                      : c.category === "fail"
                        ? "error"
                        : "primary"
                  }
                  sx={{ fontSize: "12px", height: 24, fontWeight: 600 }}
                />
              );
            })}
          </Box>
        </Box>
      );
    }

    if (outputType === "Pass/Fail") {
      return (
        <Box
          sx={{
            display: "flex",
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            alignItems: "center",
          }}
        >
          <Typography variant="caption" sx={{ flex: 1 }}>
            Result
          </Typography>
          <Box sx={{ flex: 2 }}>
            <Chip
              label={
                raw === "Passed"
                  ? "Pass"
                  : raw === "Failed"
                    ? "Fail"
                    : String(raw)
              }
              size="small"
              color={
                raw === "Passed" || raw === true || raw === 1
                  ? "success"
                  : "error"
              }
              sx={{ fontSize: "12px", height: 22 }}
            />
          </Box>
        </Box>
      );
    }

    // Scoring (numeric)
    const score = typeof raw === "number" ? raw : parseFloat(raw);
    if (!isNaN(score)) {
      return (
        <Box
          sx={{
            display: "flex",
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            alignItems: "center",
          }}
        >
          <Typography variant="caption" sx={{ flex: 1 }}>
            Score
          </Typography>
          <Box sx={{ flex: 2, display: "flex", alignItems: "center", gap: 1 }}>
            <Box
              sx={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                backgroundColor: "action.hover",
                overflow: "hidden",
              }}
            >
              <Box
                sx={{
                  width: `${Math.min(score * 100, 100)}%`,
                  height: "100%",
                  borderRadius: 3,
                  backgroundColor:
                    score >= 0.7
                      ? "success.main"
                      : score >= 0.3
                        ? "warning.main"
                        : "error.main",
                }}
              />
            </Box>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ minWidth: 35 }}
            >
              {score.toFixed(2)}
            </Typography>
          </Box>
        </Box>
      );
    }

    // Fallback
    return (
      <Box
        sx={{
          display: "flex",
          px: 1.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          alignItems: "center",
        }}
      >
        <Typography variant="caption" sx={{ flex: 1 }}>
          Result
        </Typography>
        <Box sx={{ flex: 2 }}>
          <Typography variant="caption">
            {typeof raw === "string" ? raw : JSON.stringify(raw)}
          </Typography>
        </Box>
      </Box>
    );
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {renderResult()}
      {/* Error Localization — rendered BEFORE the Explanation so the
          "running" banner stays visible at the top of the card while the
          async localizer task (30–90s) is still in flight. */}
      <ErrorLocalizationSection result={result} />
      {result.reason && (
        <Box sx={{ px: 1.5, py: 1 }}>
          <Typography variant="caption" sx={{ display: "block", mb: 0.5 }}>
            Explanation
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              fontFamily: "monospace",
              fontSize: "12px",
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              color: "text.primary",
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "divider",
              backgroundColor: (theme) =>
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.03)"
                  : "background.neutral",
            }}
          >
            {typeof result.reason === "string"
              ? result.reason
              : JSON.stringify(result.reason, null, 2)}
          </Box>
        </Box>
      )}
    </Box>
  );
};

/**
 * Normalize a single error-localizer entry for `ErrorLocalizeCard`.
 *
 * The backend returns the highlight window as snake_case
 * (`orgSen.start_idx`, `orgSen.end_idx`) and numeric fields as strings
 * (`"rank": "1"`). Before the camelCase response middleware was removed
 * this conversion was automatic; now the card can't find `startIdx`
 * and the sort+slice no-op silently. We patch each entry here so the
 * card's existing reads (`highlight.orgSen.startIdx`, `.weight`, etc.)
 * resolve correctly.
 */
function normalizeLocalizerEntry(e) {
  if (!e || typeof e !== "object") return e;
  const os = e.orgSen || {};
  const startIdx = os.startIdx ?? os.start_idx ?? 0;
  const endIdx = os.endIdx ?? os.end_idx ?? 0;
  const rankRaw = e.rank;
  const rank =
    typeof rankRaw === "number"
      ? rankRaw
      : rankRaw != null
        ? Number(rankRaw) || rankRaw
        : undefined;
  return {
    ...e,
    rank,
    // Weight drives the highlight color. Backend doesn't always emit it;
    // fall back to a rank-derived value (rank 1 = heaviest) so the card
    // still shows a colour band.
    weight:
      e.weight != null
        ? e.weight
        : typeof rank === "number"
          ? Math.max(0.3, 1 - (rank - 1) * 0.15)
          : 0.6,
    orgSen: {
      ...os,
      startIdx,
      endIdx,
      // Keep snake_case too in case anything else reads them.
      start_idx: startIdx,
      end_idx: endIdx,
    },
  };
}

function normalizeLocalizerEntries(val) {
  if (!Array.isArray(val)) return val;
  return val.map(normalizeLocalizerEntry);
}

/**
 * Renders error localization results when available.
 * Handles three states: running (spinner), completed (ErrorLocalizeCard), or absent (nothing).
 */
const ErrorLocalizationSection = ({ result }) => {
  const rawErrorDetails = result?.error_details || result?.error_analysis;
  const detailsEnvelope =
    rawErrorDetails &&
    !Array.isArray(rawErrorDetails) &&
    typeof rawErrorDetails === "object" &&
    (rawErrorDetails.error_analysis || rawErrorDetails.errorAnalysis)
      ? rawErrorDetails
      : null;
  const errorDetails =
    detailsEnvelope?.error_analysis ||
    detailsEnvelope?.errorAnalysis ||
    rawErrorDetails;
  const errorLocalizerStatus = result?.error_localizer_status;
  const errorLocalizerMessage = result?.error_localizer_message;

  // Show running indicator — the localizer runs on a ~30s Temporal tick
  // so the wait between eval finishing and highlights appearing can be
  // 30–90 seconds. Keep the banner visible the whole time so users know
  // something is still in flight.
  if (
    errorLocalizerStatus === "running" ||
    errorLocalizerStatus === "pending"
  ) {
    return (
      <Box
        sx={{
          mx: 1.5,
          my: 1,
          px: 1.5,
          py: 1.25,
          display: "flex",
          alignItems: "center",
          gap: 1.25,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: "primary.main",
          backgroundColor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(124, 77, 255, 0.08)"
              : "rgba(124, 77, 255, 0.04)",
        }}
      >
        <CircularProgress size={14} thickness={5} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="caption"
            fontWeight={600}
            sx={{ display: "block", color: "primary.main" }}
          >
            Error localization running…
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", fontSize: "11px" }}
          >
            Pinpointing which parts of the input caused the failure. This
            usually takes 30–90 seconds after the eval finishes.
          </Typography>
        </Box>
      </Box>
    );
  }

  // Surface a clear failed state so users don't assume the feature is
  // just silently broken.
  if (errorLocalizerStatus === "failed") {
    return (
      <Box
        sx={{
          mx: 1.5,
          my: 1,
          px: 1.5,
          py: 1,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: "error.light",
          backgroundColor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(255, 86, 48, 0.08)"
              : "rgba(255, 86, 48, 0.04)",
        }}
      >
        <Typography
          variant="caption"
          fontWeight={600}
          color="error.main"
          sx={{ display: "block" }}
        >
          Error localization failed
        </Typography>
        {errorLocalizerMessage && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", fontSize: "11px", mt: 0.25 }}
          >
            {errorLocalizerMessage}
          </Typography>
        )}
      </Box>
    );
  }

  if (!errorDetails) return null;

  // errorDetails can be { inputKey: [...entries] } or [...entries]
  const entries = Array.isArray(errorDetails)
    ? normalizeLocalizerEntries(errorDetails)
    : null;
  const entriesMap =
    !Array.isArray(errorDetails) && typeof errorDetails === "object"
      ? Object.fromEntries(
          canonicalEntries(errorDetails).map(([k, v]) => [
            k,
            normalizeLocalizerEntries(v),
          ]),
        )
      : null;

  const selectedInputKey =
    result?.selected_input_key ||
    result?.selectedInputKey ||
    detailsEnvelope?.selected_input_key ||
    detailsEnvelope?.selectedInputKey;
  const inputData =
    result?.input_data ||
    result?.inputData ||
    detailsEnvelope?.input_data ||
    detailsEnvelope?.inputData;
  const inputTypes =
    result?.input_types ||
    result?.inputTypes ||
    detailsEnvelope?.input_types ||
    detailsEnvelope?.inputTypes;

  // ErrorLocalizeCard reads a mix of camelCase (`datapoint.selectedInputKey`)
  // and snake_case (`datapoint.input_data[datapoint.selected_input_key]`).
  // Provide both so either path resolves.
  const datapoint = {
    selectedInputKey,
    selected_input_key: selectedInputKey,
    inputData,
    input_data: inputData,
    inputTypes,
    input_types: inputTypes,
  };

  // If no entries ended up with content, don't render anything — the eval
  // might have passed or the localizer might have found nothing to flag.
  const mapHasContent =
    entriesMap &&
    Object.values(entriesMap).some((v) => Array.isArray(v) && v.length > 0);
  const listHasContent = entries && entries.length > 0;
  if (!mapHasContent && !listHasContent) {
    if (errorLocalizerStatus === "completed") {
      return (
        <Box sx={{ px: 1.5, py: 1 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block" }}
          >
            Error Localization — no error segments found.
          </Typography>
        </Box>
      );
    }
    return null;
  }

  return (
    <Box sx={{ px: 1.5, py: 1 }}>
      <Typography
        variant="caption"
        fontWeight={600}
        sx={{ display: "block", mb: 1 }}
      >
        Error Localization
      </Typography>
      {entriesMap ? (
        Object.entries(entriesMap)
          .filter(([, v]) => v && (Array.isArray(v) ? v.length > 0 : true))
          .map(([key, value]) => (
            <ErrorLocalizeCard
              key={key}
              value={value}
              column={selectedInputKey || key}
              datapoint={datapoint}
            />
          ))
      ) : entries ? (
        <ErrorLocalizeCard
          value={entries}
          column={selectedInputKey || "input"}
          datapoint={datapoint}
        />
      ) : null}
    </Box>
  );
};

const JsonView = ({ data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const formatted = useMemo(() => JSON.stringify(data, null, 2), [data]);
  const lineCount = formatted.split("\n").length;
  const height = `${Math.min(Math.max(lineCount * 19 + 16, 100), 400)}px`;

  return (
    <Box
      sx={{
        borderRadius: "6px",
        overflow: "hidden",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Editor
        height={height}
        language="json"
        value={formatted}
        theme={isDark ? "vs-dark" : "vs"}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          fontFamily: "'Fira Code', 'JetBrains Mono', Menlo, Monaco, monospace",
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: "on",
          folding: true,
          padding: { top: 8 },
          renderLineHighlight: "none",
          domReadOnly: true,
        }}
      />
    </Box>
  );
};

EvalResultDisplay.propTypes = {
  result: PropTypes.object,
};

export default EvalResultDisplay;
