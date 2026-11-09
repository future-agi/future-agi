import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import ProductionSourceList from "./ProductionSourceList";
import useProductionImport from "./useProductionImport";

const NEW = "__new__";
const FIELDS = ["name", "input", "output", "trace_id", "id", "model", "status"];
const INITIAL_MAPPING = [
  { col_name: "input", span_field: "input", data_type: "json" },
  { col_name: "output", span_field: "output", data_type: "json" },
];

export default function ProductionDataSheet({ initial, onClose, setDirty }) {
  const source = initial.source ?? {};
  const [kind, setKind] = useState(
    source.span_ids?.length
      ? "spans"
      : source.kind === "sessions"
        ? "sessions"
        : "traces",
  );
  const [project, setProject] = useState(source.project_id ?? "");
  const [pinned, setPinned] = useState(source);
  const [failures, setFailures] = useState(
    !source.select_all &&
      !source.trace_ids?.length &&
      !source.span_ids?.length &&
      !source.session_ids?.length,
  );
  const [target, setTarget] = useState(initial.target?.dataset_id ?? NEW);
  const [name, setName] = useState(initial.target?.new?.name ?? "");
  const [mapping, setMapping] = useState(INITIAL_MAPPING);
  const [picked, setPicked] = useState([]);
  const { data: datasets = [] } = useDevelopDatasetList();
  const { data: columns = [] } = useGetDatasetColumns(
    target === NEW ? null : target,
    { enabled: target !== NEW },
  );
  const projects = useQuery({
    queryKey: ["production-import", "projects"],
    queryFn: async () =>
      (await axios.get(endpoints.project.listProjects())).data.result.projects,
  });
  const projectId = project || projects.data?.[0]?.id || "";
  const body = useMemo(
    () => ({
      project: projectId,
      source_kind: kind,
      failures_only: failures,
      ...(!pinned.select_all && pinned.trace_ids?.length
        ? { trace_ids: pinned.trace_ids }
        : {}),
      ...(!pinned.select_all && pinned.span_ids?.length
        ? { span_ids: pinned.span_ids }
        : {}),
      ...(!pinned.select_all && pinned.session_ids?.length
        ? { session_ids: pinned.session_ids }
        : {}),
    }),
    [projectId, kind, failures, pinned],
  );
  const preview = useQuery({
    queryKey: ["production-import", "preview", body],
    queryFn: async () =>
      (await axios.post(apiPath("/tracer/dataset/preview_sources/"), body)).data
        .result,
    enabled: Boolean(projectId),
    retry: false,
  });
  const items = (preview.data?.items ?? []).filter(
    (item) =>
      !pinned.select_all ||
      (!(pinned.trace_ids ?? []).includes(item.trace_id) &&
        !(pinned.span_ids ?? []).includes(item.span_id) &&
        !(pinned.session_ids ?? []).includes(item.session_id)),
  );
  useEffect(() => {
    setPicked([]);
  }, [body]);
  const selected = items.filter((item) => picked.includes(item.span_id));
  const names = mapping.map((row) => row.col_name.trim());
  const validMapping =
    names.length > 0 &&
    names.every(Boolean) &&
    new Set(names).size === names.length;
  const changeTarget = (value) => {
    setTarget(value);
    setMapping(INITIAL_MAPPING);
    setError(null);
  };
  const { submit, busy, pending, error, setError } = useProductionImport({
    projectId,
    selected,
    mapping,
    columns,
    target,
    name,
    onClose,
  });
  useEffect(() => {
    setDirty(picked.length > 0 && !pending);
  }, [picked.length, pending, setDirty]);
  return (
    <Stack
      spacing={2}
      sx={{ p: 3, overflowY: "auto", height: "100%" }}
      data-testid="add-data-sheet"
    >
      <Typography variant="h6">From production</Typography>
      <Typography color="text.secondary">
        Select Observe data from the last 7 days. Sessions import one row per
        trace, not a combined conversation.
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField
          disabled={busy || Boolean(pending)}
          select
          label="Project"
          value={projectId}
          onChange={(event) => {
            setProject(event.target.value);
            setPinned({});
          }}
          fullWidth
        >
          {(projects.data ?? []).map((item) => (
            <MenuItem key={item.id} value={item.id}>
              {item.name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          disabled={busy || Boolean(pending)}
          select
          label="Source"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value);
            setPinned({});
          }}
          fullWidth
        >
          <MenuItem value="traces">Traces</MenuItem>
          <MenuItem value="spans">Spans</MenuItem>
          <MenuItem value="sessions">Sessions</MenuItem>
        </TextField>
      </Stack>
      <FormControlLabel
        label="Failing evaluations only"
        control={
          <Checkbox
            disabled={busy || Boolean(pending)}
            checked={failures}
            onChange={(event) => setFailures(event.target.checked)}
          />
        }
      />
      {(projects.error || preview.error) && (
        <Alert severity="error">
          {getRequestErrorMessage(
            projects.error || preview.error,
            "Could not read Observe data.",
          )}
        </Alert>
      )}
      {preview.isFetching ? (
        <Typography role="status">Reading Observe data…</Typography>
      ) : (
        <Box>
          {preview.data?.limited && (
            <Alert severity="info">
              Showing matches among the newest 200 spans in this source. Narrow
              the selection in Observe to import older items.
            </Alert>
          )}
          {!items.length && (
            <Typography>No matching data in the last 7 days.</Typography>
          )}
          <ProductionSourceList
            disabled={busy || Boolean(pending)}
            items={items}
            kind={kind}
            picked={picked}
            onChange={setPicked}
          />
        </Box>
      )}
      <TextField
        disabled={busy || Boolean(pending)}
        select
        label="Add to"
        value={target}
        onChange={(event) => changeTarget(event.target.value)}
      >
        <MenuItem value={NEW}>New dataset</MenuItem>
        {
          /** @type {{ datasetId: string, name: string }[]} */ (datasets).map(
            (item) => (
              <MenuItem key={item.datasetId} value={item.datasetId}>
                {item.name}
              </MenuItem>
            ),
          )
        }
      </TextField>
      {target === NEW && (
        <TextField
          disabled={busy || Boolean(pending)}
          label="Dataset name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      )}
      <Typography variant="subtitle1">Column mapping</Typography>
      {mapping.map((row, index) => (
        <Stack key={index} direction={{ xs: "column", sm: "row" }} spacing={1}>
          <TextField
            disabled={busy || Boolean(pending)}
            label={`Column ${index + 1}`}
            value={row.col_name}
            onChange={(event) =>
              setMapping((previous) =>
                previous.map((value, position) =>
                  position === index
                    ? { ...value, col_name: event.target.value }
                    : value,
                ),
              )
            }
          />
          <TextField
            disabled={busy || Boolean(pending)}
            select
            label={`Source field ${index + 1}`}
            value={row.span_field}
            sx={{ minWidth: 180 }}
            onChange={(event) =>
              setMapping((previous) =>
                previous.map((value, position) =>
                  position === index
                    ? { ...value, span_field: event.target.value }
                    : value,
                ),
              )
            }
          >
            {FIELDS.map((field) => (
              <MenuItem key={field} value={field}>
                {field}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            disabled={busy || Boolean(pending)}
            select
            label={`Type ${index + 1}`}
            value={row.data_type}
            sx={{ minWidth: 120 }}
            onChange={(event) =>
              setMapping((previous) =>
                previous.map((value, position) =>
                  position === index
                    ? { ...value, data_type: event.target.value }
                    : value,
                ),
              )
            }
          >
            <MenuItem value="text">Text</MenuItem>
            <MenuItem value="json">JSON</MenuItem>
          </TextField>
          <Button
            disabled={busy || Boolean(pending)}
            onClick={() =>
              setMapping((previous) =>
                previous.filter((_, position) => position !== index),
              )
            }
          >
            Remove column {index + 1}
          </Button>
        </Stack>
      ))}
      <Button
        disabled={busy || Boolean(pending)}
        sx={{ alignSelf: "flex-start" }}
        onClick={() =>
          setMapping((previous) => [
            ...previous,
            { col_name: "", span_field: "name", data_type: "text" },
          ])
        }
      >
        Add column mapping
      </Button>
      {!validMapping && (
        <Alert severity="warning">
          Choose at least one mapping with unique, nonempty column names.
        </Alert>
      )}
      {pending && !error && (
        <Typography role="status">
          Import accepted. Waiting for saved rows…
        </Typography>
      )}
      {error && <Alert severity="error">{error}</Alert>}
      <Stack
        direction={{ xs: "column-reverse", sm: "row" }}
        spacing={1}
        justifyContent="flex-end"
      >
        <Button disabled={busy} onClick={() => onClose(true)}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          loading={busy}
          disabled={
            !pending &&
            (!selected.length ||
              !validMapping ||
              preview.isFetching ||
              Boolean(preview.error) ||
              (target === NEW && !name.trim()))
          }
          onClick={submit}
        >
          {pending
            ? "Check import"
            : target === NEW
              ? `Create dataset with ${selected.length} ${selected.length === 1 ? "row" : "rows"}`
              : `Add ${selected.length} ${selected.length === 1 ? "row" : "rows"}`}
        </LoadingButton>
      </Stack>
    </Stack>
  );
}
ProductionDataSheet.propTypes = {
  initial: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  setDirty: PropTypes.func.isRequired,
};
