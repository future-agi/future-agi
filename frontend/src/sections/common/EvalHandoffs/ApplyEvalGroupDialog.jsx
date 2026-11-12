// HO D9 · `Add a group` on the Data tab's and the experiment page's
// evaluation list: pick a group (AddEvalGroupButton's menu), choose its
// members (the ones already added start off), map the keys they read once,
// and Apply through eval-groups/apply-eval-group/ (DATASET or EXPERIMENT).
// On the experiment page the keys that read the run's output are fixed to
// it (Run output): an experiment eval scores its runs' stored outputs.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { gridColumnsFromConfig } from "src/sections/common/filters/gridDoorModel";
import AddEvalGroupButton from "./AddEvalGroupButton";
import {
  applyBlocked,
  applyBody,
  appliedText,
  autoMapping,
  groupKeys,
  RUN_OUTPUT_LABEL,
  runOutputMapping,
} from "./applyGroupModel";

const errorText = (e) =>
  (typeof e?.result === "string" && e.result) ||
  (typeof e?.response?.data?.result === "string" && e.response.data.result) ||
  e?.message ||
  "Couldn't apply the group";

function ApplyBody({
  pick,
  page,
  datasetId,
  experimentId,
  savedEvals,
  onDone,
}) {
  const members = pick.detail?.members ?? [];
  const name = pick.detail?.eval_group?.name ?? pick.group?.name ?? "";
  const already = new Set(pick.skipped.map((s) => s.member.eval_template_id));
  const [deselected, setDeselected] = useState([...already]);
  const { data: table } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], ""),
  );
  const columns = useMemo(
    () => gridColumnsFromConfig(table?.data?.result?.column_config),
    [table],
  );
  const keys = groupKeys(members, deselected);
  const auto = useMemo(
    () =>
      autoMapping([...keys.required, ...keys.optional], columns, savedEvals),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [columns, savedEvals, keys.required.join(), keys.optional.join()],
  );
  const fixed = useMemo(
    () => runOutputMapping([...keys.required, ...keys.optional], page),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [page, keys.required.join(), keys.optional.join()],
  );
  const [picked, setPicked] = useState({});
  const mapping = { ...auto, ...picked, ...fixed };
  const blocked = applyBlocked({ members, deselected, mapping });
  const apply = useMutation({
    mutationFn: () =>
      axios.post(
        endpoints.develop.eval.applyEvalGroup,
        applyBody({
          groupId: pick.group.id,
          page,
          datasetId,
          experimentId,
          mapping,
          deselected,
        }),
      ),
    onSuccess: () => {
      const n = members.length - deselected.length;
      enqueueSnackbar(appliedText(name, n, page), { variant: "success" });
      onDone(true);
    },
    onError: (e) => enqueueSnackbar(errorText(e), { variant: "error" }),
  });
  const toggle = (id) =>
    setDeselected((d) =>
      d.includes(id) ? d.filter((x) => x !== id) : [...d, id],
    );
  const keyField = (k, required) =>
    fixed[k] ? (
      <TextField
        key={k}
        size="small"
        label={required ? k : `${k} (optional)`}
        value={RUN_OUTPUT_LABEL}
        InputProps={{ readOnly: true }}
        sx={{ minWidth: 220, flex: 1 }}
        inputProps={{
          "data-testid": `apply-group-key-${k}`,
          "data-fixed": fixed[k],
        }}
      />
    ) : (
      <TextField
        key={k}
        select
        size="small"
        label={required ? k : `${k} (optional)`}
        value={mapping[k] ?? ""}
        onChange={(e) => setPicked((m) => ({ ...m, [k]: e.target.value }))}
        sx={{ minWidth: 220, flex: 1 }}
        inputProps={{ "data-testid": `apply-group-key-${k}` }}
      >
        {!required ? <MenuItem value="">None</MenuItem> : null}
        {columns.map((c) => (
          <MenuItem key={c.id} value={c.id}>
            {c.name}
          </MenuItem>
        ))}
      </TextField>
    );
  return (
    <>
      <DialogContent>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
          {page === "experiment"
            ? "Each evaluation is added to this experiment and scores its runs' stored outputs."
            : "Each evaluation is added to this dataset. Run them from this list to score the rows."}
        </Typography>
        <Stack data-testid="apply-group-members">
          {members.map((m) => (
            <FormControlLabel
              key={m.eval_template_id}
              control={
                <Checkbox
                  size="small"
                  checked={!deselected.includes(m.eval_template_id)}
                  onChange={() => toggle(m.eval_template_id)}
                />
              }
              label={
                <Typography typography="s2">
                  {m.name}
                  {already.has(m.eval_template_id) ? (
                    <Typography
                      component="span"
                      typography="s3"
                      color="text.secondary"
                    >
                      {" · already added"}
                    </Typography>
                  ) : null}
                </Typography>
              }
            />
          ))}
        </Stack>
        {keys.required.length || keys.optional.length ? (
          <Box sx={{ mt: 1.5 }}>
            <Typography typography="s2" fontWeight="fontWeightMedium">
              Map the keys they read
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={1.5} sx={{ mt: 1 }}>
              {keys.required.map((k) => keyField(k, true))}
              {keys.optional.map((k) => keyField(k, false))}
            </Stack>
          </Box>
        ) : null}
        {blocked ? (
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ mt: 1.5 }}
            data-testid="apply-group-blocked"
          >
            {blocked}
          </Typography>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={() => onDone(false)}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          disabled={Boolean(blocked)}
          loading={apply.isPending}
          onClick={() => apply.mutate()}
          data-testid="apply-group-apply"
        >
          {`Apply ${name}`}
        </LoadingButton>
      </DialogActions>
    </>
  );
}

ApplyBody.propTypes = {
  pick: PropTypes.object.isRequired,
  page: PropTypes.oneOf(["dataset", "experiment"]).isRequired,
  datasetId: PropTypes.string.isRequired,
  experimentId: PropTypes.string,
  savedEvals: PropTypes.array,
  onDone: PropTypes.func.isRequired,
};

/** The button and its dialog; `onApplied()` refreshes the host's list. */
export default function ApplyEvalGroupButton({
  page,
  datasetId,
  experimentId,
  savedEvals = [],
  onApplied,
}) {
  const [pick, setPick] = useState(null);
  const existing = (savedEvals ?? []).map((e) => ({
    templateId: e.template_id ?? e.templateId,
    name: e.name,
  }));
  return (
    <>
      <AddEvalGroupButton existing={existing} onPick={setPick} />
      <Dialog
        open={Boolean(pick)}
        onClose={() => setPick(null)}
        maxWidth={false}
        PaperProps={{ sx: { width: 560 } }}
        data-testid="apply-group-dialog"
      >
        {pick ? (
          <>
            <DialogTitle>
              {`Add ${pick.detail?.eval_group?.name ?? pick.group?.name} to this ${page}`}
            </DialogTitle>
            <ApplyBody
              pick={pick}
              page={page}
              datasetId={datasetId}
              experimentId={experimentId}
              savedEvals={savedEvals}
              onDone={(applied) => {
                setPick(null);
                if (applied) onApplied?.();
              }}
            />
          </>
        ) : null}
      </Dialog>
    </>
  );
}

ApplyEvalGroupButton.propTypes = {
  page: PropTypes.oneOf(["dataset", "experiment"]).isRequired,
  datasetId: PropTypes.string.isRequired,
  experimentId: PropTypes.string,
  savedEvals: PropTypes.array,
  onApplied: PropTypes.func,
};
