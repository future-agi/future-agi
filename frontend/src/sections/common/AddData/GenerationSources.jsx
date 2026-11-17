import React from "react";
import PropTypes from "prop-types";
import { Alert, MenuItem, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

export default function GenerationSources({
  datasetId,
  rowIds,
  kbId,
  onChange,
  disabled,
}) {
  const query = useQuery({
    queryKey: ["generation-knowledge-bases", datasetId],
    queryFn: () =>
      axios
        .get(apiPath("/model-hub/knowledge-base/list/"), {
          params: { status: true },
        })
        .then((response) => response.data.result.table_data ?? []),
    retry: false,
  });
  return (
    <>
      {rowIds.length > 0 && (
        <Typography variant="body2">
          {`Using only ${rowIds.length} selected ${rowIds.length === 1 ? "row" : "rows"} as examples. Maximum 50.`}
        </Typography>
      )}
      {query.error ? (
        <Alert severity="warning">
          Unable to load knowledge bases. Close and reopen to retry.
        </Alert>
      ) : null}
      <TextField
        select
        label="Knowledge base"
        value={kbId}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || query.isPending || Boolean(query.error)}
        helperText="Optional grounding from up to 20 indexed passages. Requires text columns named context and citation; both are validated against the source."
      >
        <MenuItem value="">None</MenuItem>
        {(query.data ?? []).map((kb) => (
          <MenuItem key={kb.id} value={kb.id}>
            {kb.name}
          </MenuItem>
        ))}
        {kbId && query.data && !query.data.some((kb) => kb.id === kbId) && (
          <MenuItem value={kbId}>Saved knowledge base unavailable</MenuItem>
        )}
      </TextField>
    </>
  );
}
GenerationSources.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowIds: PropTypes.array.isRequired,
  kbId: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
