import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Collapse,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { evaluatorBinding, evaluatorDrift } from "./metricForm";
import useMetricEvaluatorNames from "./use_metric_evaluator_names";

const LIST_LIMIT = 50;

const sameEvaluator = (option, value) => option.id === value.id;

const definitionLabel = (hash) =>
  hash ? `definition ${hash.slice(0, 8)}` : "legacy unversioned results";

/**
 * Bind an evaluation input to one of the project's evaluators. Selecting fills
 * BOTH the evaluator config id and the config_hash its results are persisted
 * under, so a sampled (weighted) metric finds its results without the author
 * having to know either value. The raw id/hash fields stay available as an
 * advanced fallback.
 */
export default function EvaluatorBinding({
  index,
  input,
  projectId,
  disabled,
  readOnly,
  onBind,
  onField,
}) {
  const [text, setText] = useState("");
  const [advanced, setAdvanced] = useState(
    Boolean(input.config_id) && !input.config_hash,
  );
  const search = useDebounce(text.trim(), 250);
  const listing = useQuery({
    queryKey: ["derived-metric-evaluators", projectId, search],
    enabled: Boolean(projectId),
    staleTime: 30_000,
    queryFn: async ({ signal }) =>
      (
        await axios.get(endpoints.derivedMetrics.evaluators, {
          signal,
          params: { project_id: projectId, search, limit: LIST_LIMIT },
        })
      ).data,
  });
  const rows = listing.data?.results ?? [];
  const listed = rows.find((row) => row.id === input.config_id);
  const names = useMetricEvaluatorNames(projectId, [input], "", rows);
  // The bound evaluator stays an option even when the current search filters it
  // out of the list, so the control never shows a value it cannot resolve.
  const selected = input.config_id
    ? listed ?? {
        id: input.config_id,
        name: names[input.config_id],
        config_hash: input.config_hash,
      }
    : null;
  const options =
    selected && !listed ? [selected, ...rows] : rows.slice(0, LIST_LIMIT);
  const drifted = evaluatorDrift(input, listed);
  const label = `Input ${index + 1} evaluator`;
  const helper = !projectId
    ? "Select a project first."
    : listing.isError
      ? "Evaluators could not be loaded. Retry, or use the advanced fields."
      : listing.data?.truncated
        ? `Showing the first ${LIST_LIMIT} matches. Type to narrow the list.`
        : "Results are read under the evaluator's exact definition.";
  return (
    <Stack spacing={1} sx={{ width: "100%" }}>
      <Autocomplete
        readOnly={readOnly}
        size="small"
        fullWidth
        disabled={disabled || !projectId}
        loading={listing.isFetching}
        noOptionsText="No saved project evaluators match. Configure an evaluator in a task first; this metric does not create or execute evaluators."
        options={options}
        value={selected}
        // The server filters by name; client filtering would hide the match.
        filterOptions={(all) => all}
        isOptionEqualToValue={sameEvaluator}
        getOptionLabel={(option) =>
          option.name || "Saved evaluator (name unavailable; binding preserved)"
        }
        onInputChange={(_, value, reason) => {
          if (reason === "input") setText(value);
          if (reason === "clear") setText("");
        }}
        onChange={(_, evaluator) => {
          if (!disabled && !readOnly) onBind(evaluatorBinding(evaluator));
        }}
        renderOption={(liProps, option) => {
          // MUI passes `key` inside the props; it must not be spread into JSX.
          const { key, ...optionProps } = liProps;
          return (
            <li {...optionProps} key={option.id || key}>
              <Box>
                <Typography variant="body2">
                  {option.name ||
                    "Saved evaluator (name unavailable; binding preserved)"}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {definitionLabel(option.config_hash)}
                </Typography>
              </Box>
            </li>
          );
        }}
        renderInput={(params) => (
          <TextField {...params} label={label} helperText={helper} />
        )}
      />
      {drifted && (
        <Alert severity="warning" role="note">
          This evaluator&apos;s definition changed after the input was bound.
          New results are stored under a different hash, so this input still
          reads only the older results. Select the evaluator again to follow its
          current definition.
        </Alert>
      )}
      <Button
        size="small"
        color="inherit"
        sx={{ alignSelf: "flex-start" }}
        aria-expanded={advanced}
        onClick={() => setAdvanced((open) => !open)}
      >
        {`Advanced: ${advanced ? "hide" : "show"} evaluator ID and hash for input ${index + 1}`}
      </Button>
      <Collapse in={advanced}>
        <Stack direction={{ xs: "column", md: "row" }} gap={1}>
          <TextField
            fullWidth
            size="small"
            label={`Input ${index + 1} evaluator ID`}
            value={input.config_id || ""}
            disabled={disabled}
            InputProps={{ readOnly }}
            onChange={(e) => {
              if (!disabled && !readOnly) onField("config_id", e.target.value);
            }}
          />
          <TextField
            fullWidth
            size="small"
            label={`Input ${index + 1} evaluator hash`}
            value={input.config_hash || ""}
            disabled={disabled}
            InputProps={{ readOnly }}
            onChange={(e) => {
              if (!disabled && !readOnly)
                onField("config_hash", e.target.value);
            }}
            helperText="Exact persisted config_hash. Empty selects legacy unversioned results only; sampled results always carry a hash."
          />
        </Stack>
      </Collapse>
    </Stack>
  );
}

EvaluatorBinding.propTypes = {
  index: PropTypes.number.isRequired,
  input: PropTypes.object.isRequired,
  projectId: PropTypes.string,
  disabled: PropTypes.bool,
  readOnly: PropTypes.bool,
  onBind: PropTypes.func.isRequired,
  onField: PropTypes.func.isRequired,
};
