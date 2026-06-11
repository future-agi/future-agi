/* eslint-disable react/prop-types */
/**
 * TestRetrievalPanel — JSON-input test surface for the Ground Truth tab.
 *
 * Mirrors the Eval Playground "Test Evaluation → Custom" surface: the
 * user types a JSON object whose keys are the rule prompt's
 * `{{template_variable}}` placeholders, optionally remaps each variable
 * to a top-level (or dotted) JSON key, and clicks Test retrieval. The
 * resolved `{variable: value}` dict is sent verbatim to
 * `/ground-truth/<id>/search/` — same shape the eval runtime sends, so
 * the test mirrors a real eval call.
 */

import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import { useSearchGroundTruth } from "../hooks/useGroundTruth";
import { CustomJsonInput } from "./TestPlayground";

// Render each match the same way it'll appear as a few-shot example
// in the eval prompt: the mapped inputs first, then the labelled output
// + (optional) explanation. Unmapped columns are tucked behind a
// "show raw row" toggle so they don't drown the signal.
function MatchRow({ label, value }) {
  if (value == null || value === "") return null;
  const display =
    typeof value === "object" ? JSON.stringify(value) : String(value);
  return (
    <Box sx={{ display: "flex", gap: 0.75, mb: 0.5 }}>
      <Typography
        variant="caption"
        sx={{ color: "text.secondary", fontWeight: 600, flexShrink: 0 }}
      >
        {label}:
      </Typography>
      <Typography
        variant="caption"
        sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {display}
      </Typography>
    </Box>
  );
}

function MatchCard({ result, variableMapping, roleMapping }) {
  const row = result.row_data || {};

  // variable_mapping: {template_var: gt_col | [gt_col, ...]}
  // Flatten so each (var, col) pair becomes its own input line.
  const inputEntries = Object.entries(variableMapping || {}).flatMap(
    ([variable, cols]) =>
      (Array.isArray(cols) ? cols : [cols])
        .filter(Boolean)
        .map((col) => ({ variable, col })),
  );

  const outputCol =
    roleMapping?.output || roleMapping?.expected_output || null;
  const explanationCol =
    roleMapping?.explanation ||
    roleMapping?.reasoning ||
    roleMapping?.reason ||
    null;

  return (
    <Box
      sx={{
        p: 1.5,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1 }}
      >
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          Match #{result._idx}
        </Typography>
        <Chip
          size="small"
          label={`similarity ${Number(result.similarity).toFixed(3)}`}
          variant="outlined"
        />
      </Stack>

      {inputEntries.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography
            variant="overline"
            sx={{ color: "text.secondary", letterSpacing: 0.5 }}
          >
            Inputs
          </Typography>
          {inputEntries.map(({ variable, col }) => (
            <MatchRow
              key={`${variable}-${col}`}
              label={
                variable === col
                  ? `{{${variable}}}`
                  : `{{${variable}}} ← ${col}`
              }
              value={row[col]}
            />
          ))}
        </Box>
      )}

      {(outputCol || explanationCol) && (
        <Box>
          <Typography
            variant="overline"
            sx={{ color: "text.secondary", letterSpacing: 0.5 }}
          >
            Label
          </Typography>
          {outputCol && (
            <MatchRow label={`Output (${outputCol})`} value={row[outputCol]} />
          )}
          {explanationCol && (
            <MatchRow
              label={`Explanation (${explanationCol})`}
              value={row[explanationCol]}
            />
          )}
        </Box>
      )}
    </Box>
  );
}

function ResultsList({ results, variableMapping, roleMapping }) {
  if (!results?.length) {
    return (
      <Alert severity="info" sx={{ mt: 2 }}>
        No matches yet. Run a test above.
      </Alert>
    );
  }
  return (
    <Stack spacing={1.5} sx={{ mt: 2 }}>
      {results.map((r, idx) => (
        <MatchCard
          key={idx}
          result={{ ...r, _idx: idx + 1 }}
          variableMapping={variableMapping}
          roleMapping={roleMapping}
        />
      ))}
    </Stack>
  );
}

export default function TestRetrievalPanel({
  groundTruthId,
  rulePrompt,
  variableMapping,
  roleMapping,
  embeddingStatus,
  embeddingsStale,
}) {
  const [customValues, setCustomValues] = useState({});
  const [maxResults, setMaxResults] = useState(3);
  const [similarityThreshold, setSimilarityThreshold] = useState(0);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  const ruleVariables = useMemo(
    () => extractJinjaVariables(rulePrompt || ""),
    [rulePrompt],
  );

  const ready = embeddingStatus === "completed" && !embeddingsStale;

  const search = useSearchGroundTruth();

  const handleCustomChange = (variable, value) =>
    setCustomValues((prev) => ({ ...prev, [variable]: value }));

  const runSearch = useCallback(async () => {
    setError(null);
    setResults(null);
    const inputs = {};
    ruleVariables.forEach((v) => {
      const value = customValues[v];
      if (value !== undefined && value !== null && String(value).trim()) {
        inputs[v] = value;
      }
    });
    if (!Object.keys(inputs).length) {
      setError(
        "Provide a value for at least one template variable before testing.",
      );
      return;
    }
    try {
      const data = await search.mutateAsync({
        gtId: groundTruthId,
        inputs,
        maxResults,
        similarityThreshold,
      });
      setResults(data?.results || []);
    } catch (e) {
      setError(
        e?.response?.data?.detail ||
          e?.response?.data?.message ||
          e?.message ||
          "Search failed.",
      );
    }
  }, [
    customValues,
    ruleVariables,
    groundTruthId,
    maxResults,
    search,
    similarityThreshold,
  ]);

  return (
    <Box>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="subtitle2">Test retrieval</Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Paste a JSON object whose keys match the rule prompt's variables.
        </Typography>
      </Stack>

      {!ready && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          {embeddingsStale
            ? "Embeddings are stale (mapping changed). Re-embed before testing."
            : `Embeddings are ${embeddingStatus}. Test results will be available once status reaches "completed".`}
        </Alert>
      )}

      <CustomJsonInput
        variables={ruleVariables}
        inputValues={customValues}
        onInputChange={handleCustomChange}
        instructions=""
      />

      <Divider sx={{ my: 2 }} />

      <Stack direction="row" spacing={1.5} alignItems="center">
        <TextField
          size="small"
          label="Top K"
          type="number"
          value={maxResults}
          onChange={(e) =>
            setMaxResults(
              Math.max(1, Math.min(20, Number(e.target.value) || 1)),
            )
          }
          sx={{ width: 100 }}
          inputProps={{ min: 1, max: 20 }}
        />
        <TextField
          size="small"
          label="Min similarity"
          type="number"
          value={similarityThreshold}
          onChange={(e) =>
            setSimilarityThreshold(
              Math.max(0, Math.min(1, Number(e.target.value) || 0)),
            )
          }
          sx={{ width: 140 }}
          inputProps={{ min: 0, max: 1, step: 0.05 }}
        />
        <Button
          variant="contained"
          onClick={runSearch}
          disabled={search.isPending || !ready}
          startIcon={
            search.isPending ? (
              <CircularProgress size={14} />
            ) : (
              <Iconify icon="eva:search-outline" width={16} />
            )
          }
        >
          Test retrieval
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          {error}
        </Alert>
      )}

      {results !== null && (
        <ResultsList
          results={results}
          variableMapping={variableMapping}
          roleMapping={roleMapping}
        />
      )}
    </Box>
  );
}

TestRetrievalPanel.propTypes = {
  groundTruthId: PropTypes.string.isRequired,
  rulePrompt: PropTypes.string,
  variableMapping: PropTypes.object,
  roleMapping: PropTypes.object,
  embeddingStatus: PropTypes.string,
  embeddingsStale: PropTypes.bool,
};
