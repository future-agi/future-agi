// W6 B5 (W6-15): "Retrieved at run time from [KB ▾] · top [n] · query from
// [column ▾]" for one template variable. Writes the card's contextSource
// (contextSourceModel's one shape; cardExtras/endpoint.js sends it as the
// create body's context_source). The knowledge bases come from the KB list
// (GET knowledge-base/get/); with none the field is not shown. Mounted by a
// integration on the Data step (options-truth W6/w6-endpoints REPORT.md).
import React from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import {
  contextSourceIssues,
  contextSourceSummary,
} from "./contextSourceModel";

/** The workspace's knowledge bases `[{id, name}]`; null while loading. */
function useKnowledgeBases() {
  const { data, isSuccess } = useQuery({
    queryKey: ["knowledge-bases", "context-source"],
    queryFn: () =>
      axios.get(endpoints.knowledge.list, {
        params: { page_number: 0, page_size: 100 },
      }),
    select: (res) =>
      (res?.data?.result?.table_data ?? []).map((k) => ({
        id: k.id,
        name: k.name,
      })),
    staleTime: 60_000,
  });
  return isSuccess ? data ?? [] : null;
}

export function ContextSourceFieldView({
  value,
  onChange,
  variables,
  columns,
  kbs,
}) {
  if (!kbs?.length) return null;
  const v = value ?? {};
  const set = (patch) => onChange({ ...v, ...patch });
  const issues = contextSourceIssues(value, { variables, columns, kbs });
  const kb = kbs.find((k) => k.id === v.kb_id);
  const query = columns.find((c) => c.id === v.query_column);
  const summary = contextSourceSummary(value, {
    kbName: kb?.name,
    queryName: query?.name,
  });
  return (
    <Stack spacing={0.75} data-testid="context-source">
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Variable"
          value={v.variable ?? ""}
          onChange={(e) => set({ variable: e.target.value })}
          sx={{ minWidth: 140 }}
          inputProps={{ "data-testid": "context-source-variable" }}
        >
          {variables.map((name) => (
            <MenuItem key={name} value={name}>
              {`{{${name}}}`}
            </MenuItem>
          ))}
        </TextField>
        <Typography typography="s2" color="text.secondary">
          retrieved at run time from
        </Typography>
        <TextField
          select
          size="small"
          label="Knowledge base"
          value={v.kb_id ?? ""}
          onChange={(e) => set({ kb_id: e.target.value })}
          sx={{ minWidth: 180 }}
          inputProps={{ "data-testid": "context-source-kb" }}
        >
          {kbs.map((k) => (
            <MenuItem key={k.id} value={k.id}>
              {k.name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          label="Top"
          type="number"
          value={v.top_k ?? ""}
          onChange={(e) => set({ top_k: e.target.value })}
          sx={{ width: 90 }}
          inputProps={{ min: 1, step: 1, "data-testid": "context-source-top" }}
        />
        <TextField
          select
          size="small"
          label="Query from"
          value={v.query_column ?? ""}
          onChange={(e) => set({ query_column: e.target.value })}
          sx={{ minWidth: 180 }}
          inputProps={{ "data-testid": "context-source-query" }}
        >
          {columns.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {c.name}
            </MenuItem>
          ))}
        </TextField>
        <Typography
          component="button"
          type="button"
          typography="s2"
          color="primary.main"
          onClick={() => onChange(null)}
          data-testid="context-source-remove"
          sx={{ border: 0, bgcolor: "transparent", cursor: "pointer", p: 0 }}
        >
          Map a column instead
        </Typography>
      </Stack>
      {issues.length ? (
        <Typography
          typography="s2"
          color="error.main"
          data-testid="context-source-issues"
        >
          {issues.join(" ")}
        </Typography>
      ) : summary ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="context-source-summary"
        >
          {`${summary} · each try stores the chunks it sent`}
        </Typography>
      ) : null}
    </Stack>
  );
}
ContextSourceFieldView.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  variables: PropTypes.arrayOf(PropTypes.string).isRequired,
  columns: PropTypes.arrayOf(PropTypes.object).isRequired,
  kbs: PropTypes.arrayOf(PropTypes.object),
};

export default function ContextSourceField(props) {
  const kbs = useKnowledgeBases();
  return <ContextSourceFieldView {...props} kbs={kbs} />;
}
ContextSourceField.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  variables: PropTypes.arrayOf(PropTypes.string).isRequired,
  columns: PropTypes.arrayOf(PropTypes.object).isRequired,
};
