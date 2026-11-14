import PropTypes from "prop-types";
import { useMemo } from "react";
import { Alert, Button, MenuItem, Stack, TextField } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import {
  getDatasetQueryOptions,
  useDatasetColumnConfig,
} from "src/api/develop/develop-detail";
import { MAX_LABEL_COLUMNS } from "src/api/simulate-environments/evidenceImports";
import ContentSkeleton from "src/components/content-skeleton";
import { EVIDENCE_COPY } from "./evidence.constants";

// Columns whose cells are media, not text: never a conversation, a label or an
// expected outcome (the backend refuses them too).
const MEDIA_TYPES = new Set(["audio", "image", "images", "document"]);

// Which of a dataset's columns its rows are read through: the conversation,
// the labels and the expected outcome. Each column is picked at most once.
export default function DatasetColumnsFields({ datasetId, value, onChange }) {
  const all = useDatasetColumnConfig(datasetId, true, true);
  // The same query the columns come from (one request): its failure state.
  const { isError, refetch } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], "", { enabled: true }),
  );
  const columns = useMemo(
    () =>
      all
        .filter((one) => one?.field && !MEDIA_TYPES.has(one?.dataType))
        .map((one) => ({ id: one.field, name: one.headerName || one.field })),
    [all],
  );
  const set = (patch) => onChange({ ...value, ...patch });
  const others = (...taken) =>
    columns.filter((one) => !taken.flat().includes(one.id));

  if (isError && !all.length) {
    return (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={() => refetch()}>
            {EVIDENCE_COPY.retry}
          </Button>
        }
      >
        {EVIDENCE_COPY.columnsError}
      </Alert>
    );
  }
  if (!all.length) {
    return (
      <ContentSkeleton
        variant="rows"
        rows={3}
        label={EVIDENCE_COPY.columnsLoading}
      />
    );
  }
  if (!columns.length) {
    return <Alert severity="info">{EVIDENCE_COPY.noTextColumn}</Alert>;
  }
  return (
    <Stack spacing={2}>
      <TextField
        select
        size="small"
        label={EVIDENCE_COPY.conversationColumn}
        helperText={EVIDENCE_COPY.conversationHelp}
        value={value.conversation}
        onChange={(event) =>
          set({
            conversation: event.target.value,
            labels: value.labels.filter((id) => id !== event.target.value),
            expected:
              value.expected === event.target.value ? "" : value.expected,
          })
        }
        inputProps={{ "data-testid": "evidence-conversation-column" }}
      >
        {columns.map((one) => (
          <MenuItem key={one.id} value={one.id}>
            {one.name}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        select
        size="small"
        label={EVIDENCE_COPY.labelColumns}
        helperText={EVIDENCE_COPY.labelColumnsHelp}
        value={value.labels}
        SelectProps={{ multiple: true }}
        onChange={(event) => set({ labels: event.target.value })}
        inputProps={{ "data-testid": "evidence-label-columns" }}
      >
        {others(value.conversation, value.expected).map((one) => (
          <MenuItem
            key={one.id}
            value={one.id}
            disabled={
              value.labels.length >= MAX_LABEL_COLUMNS &&
              !value.labels.includes(one.id)
            }
          >
            {one.name}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        select
        size="small"
        label={EVIDENCE_COPY.expectedColumn}
        helperText={EVIDENCE_COPY.expectedHelp}
        value={value.expected}
        onChange={(event) => set({ expected: event.target.value })}
        inputProps={{ "data-testid": "evidence-expected-column" }}
      >
        <MenuItem value="">{EVIDENCE_COPY.noColumn}</MenuItem>
        {others(value.conversation, value.labels).map((one) => (
          <MenuItem key={one.id} value={one.id}>
            {one.name}
          </MenuItem>
        ))}
      </TextField>
    </Stack>
  );
}

DatasetColumnsFields.propTypes = {
  datasetId: PropTypes.string.isRequired,
  value: PropTypes.shape({
    conversation: PropTypes.string.isRequired,
    labels: PropTypes.arrayOf(PropTypes.string).isRequired,
    expected: PropTypes.string.isRequired,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
};
