// Classification setup (W5-4 B3), one per dataset: Truth [column], Predicted
// [output.intent], Positive class (optional), with the labels found in the
// data for the picked column, and an optional map from predicted labels the
// truth column never uses onto its labels. Save PUTs the setup.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import {
  apiErrorText,
  w5Path,
} from "src/sections/common/Analysis/workbench/w5Api";
import {
  CLASSIFICATION_KEY,
  PREDICTED_HELP,
  unmatchedLabels,
} from "./classificationModel";
const NONE = "__none__";

export default function ClassificationSetupDialog({
  open,
  onClose,
  datasetId,
  runIds,
  columns,
  setup,
}) {
  const qc = useQueryClient();
  const path = w5Path("classification", datasetId);
  const [draft, setDraft] = useState({
    truth_column: setup?.truth_column ?? "",
    predicted: setup?.predicted ?? "output",
    positive: setup?.positive ?? NONE,
    label_map: setup?.label_map ?? {},
  });
  const predicted = useDebounce(draft.predicted, 250);
  const preview = useQuery({
    queryKey: [
      CLASSIFICATION_KEY,
      datasetId,
      "draft",
      runIds.join(","),
      draft.truth_column,
      predicted,
    ],
    queryFn: () =>
      axios.get(path, {
        params: {
          runs: runIds.join(","),
          truth_column: draft.truth_column,
          predicted,
        },
      }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && draft.truth_column && runIds.length),
    retry: false,
  });
  const save = useMutation({
    mutationFn: (body) => axios.put(path, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [CLASSIFICATION_KEY, datasetId] });
      onClose(true);
    },
  });
  const labels = preview.data?.truth_labels ?? [];
  const unmatched = unmatchedLabels(preview.data?.predicted_labels, labels);
  const mapTo = (from, to) =>
    setDraft((d) => {
      const next = { ...d.label_map };
      if (to === NONE) delete next[from];
      else next[from] = to;
      return { ...d, label_map: next };
    });
  const parsed = preview.data
    ? Object.values(preview.data.runs ?? {}).reduce((s, r) => s + r.n, 0)
    : null;
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  return (
    <Dialog open={open} onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>Classification setup</DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }}>
          <TextField
            select
            size="small"
            label="Truth"
            value={draft.truth_column}
            onChange={set("truth_column")}
            inputProps={{ "aria-label": "Truth" }}
            helperText="The column that holds the right label"
          >
            {columns.map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="Predicted"
            value={draft.predicted}
            onChange={set("predicted")}
            helperText={
              preview.isError ? apiErrorText(preview.error) : PREDICTED_HELP
            }
            error={preview.isError}
            inputProps={{
              "aria-label": "Predicted",
              spellCheck: false,
              style: { fontFamily: "monospace" },
            }}
          />
          <TextField
            select
            size="small"
            label="Positive class (optional)"
            value={labels.includes(draft.positive) ? draft.positive : NONE}
            onChange={set("positive")}
            inputProps={{ "aria-label": "Positive class" }}
          >
            <MenuItem value={NONE}>None (every class counts the same)</MenuItem>
            {labels.map((l) => (
              <MenuItem key={l} value={l}>
                {l}
              </MenuItem>
            ))}
          </TextField>
          {draft.truth_column && preview.data && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="classification-labels"
            >
              {`Labels: from the data (${labels.length}) · ${parsed} predictions read on the runs in view`}
            </Typography>
          )}
          {draft.truth_column && unmatched.length > 0 && (
            <Stack gap={1} data-testid="classification-label-map">
              <Typography typography="s2">
                {`Map predicted labels (optional): ${unmatched.length} predicted label${unmatched.length === 1 ? " is" : "s are"} not in the truth column`}
              </Typography>
              {unmatched.map((from) => (
                <Stack key={from} direction="row" alignItems="center" gap={1}>
                  <Typography
                    typography="s2"
                    sx={{ minWidth: 140, fontFamily: "monospace" }}
                  >
                    {from}
                  </Typography>
                  <Typography typography="s2" color="text.secondary">
                    counts as
                  </Typography>
                  <TextField
                    select
                    size="small"
                    value={draft.label_map[from] ?? NONE}
                    onChange={(e) => mapTo(from, e.target.value)}
                    inputProps={{ "aria-label": `Map ${from}` }}
                    sx={{ minWidth: 200 }}
                  >
                    <MenuItem value={NONE}>itself (no match)</MenuItem>
                    {labels.map((l) => (
                      <MenuItem key={l} value={l}>
                        {l}
                      </MenuItem>
                    ))}
                  </TextField>
                </Stack>
              ))}
            </Stack>
          )}
          {save.isError && (
            <Typography typography="s2" color="error.main">
              {apiErrorText(save.error)}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button
          variant="contained"
          disabled={
            !draft.truth_column ||
            !draft.predicted ||
            preview.isError ||
            save.isPending
          }
          onClick={() =>
            save.mutate({
              truth_column: draft.truth_column,
              predicted: draft.predicted.trim(),
              positive: draft.positive === NONE ? null : draft.positive,
              label_map: Object.fromEntries(
                Object.entries(draft.label_map).filter(([k]) =>
                  unmatched.includes(k),
                ),
              ),
            })
          }
        >
          Save setup
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ClassificationSetupDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string.isRequired,
  runIds: PropTypes.array.isRequired,
  columns: PropTypes.array.isRequired,
  setup: PropTypes.object,
};
