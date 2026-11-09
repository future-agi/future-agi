import { formatExactMoney } from "src/sections/common/format/money";
// the interface contract-8a (lane w3-rows-in A9): Generate rows into the draft. Opened from
// Add rows › Generate rows and the selection's `Generate more like these`.
// The server drafts the rows (IM1 `source.kind: "generate"`): near
// duplicates start unticked, each row can be edited or regenerated, and
// the ticked ones land as pending rows (Needs review); Save version keeps
// them. Shapes: src/_mock/prototype/CONTRACT.md "the interface contract · implementation".
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery } from "@tanstack/react-query";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { create } from "zustand";
import axios from "src/utils/axios";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { useDebounce } from "src/hooks/use-debounce";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { useVersionDialogs } from "src/sections/develop-detail/DataTab/Versions/versionDialogsStore";
import { revealNewRows } from "src/sections/develop-detail/DataTab/DataGrid/newRowsReveal";
import { toPendingOps } from "src/sections/develop/AddRowDrawer/fileImport";
import RunOnRowsButton from "src/sections/common/RunSources/RunOnRowsButton";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { IMPORTS_PATH } from "./mappingModel";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";
import ImmediateGenerateRows from "./ImmediateGenerateRows";

/** `useGenerateRows.getState().open({datasetId, rowIds})` opens the dialog. */
export const useGenerateRows = create((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));

const GENERATE_MAX = 100;
const money = (usd) => (usd == null ? null : `About ${formatExactMoney(usd)}`);
const short = (v) =>
  v == null || v === ""
    ? "—"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);

/** Keep these fields applies when drafts come from rows. */
export const keepOffered = (basis) => basis === "rows" || basis === "all";

function sourceOf(form, rowIds, attempts) {
  return {
    kind: "generate",
    basis: form.basis,
    ...(form.basis === "rows" ? { row_ids: rowIds } : {}),
    ...(form.basis === "description" ? { description: form.description } : {}),
    ...(form.basis === "kb" ? { kb_id: form.kbId } : {}),
    n: Number(form.n),
    keep: keepOffered(form.basis) ? form.keep : [],
    ...(form.spread ? { spread: form.spread } : {}),
    instructions: form.instructions,
    model: form.model,
    ...(attempts && Object.keys(attempts).length ? { attempts } : {}),
  };
}

function EditableCell({ value, onChange, testId }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(short(value));
  useEffect(() => setText(short(value)), [value]);
  if (editing)
    return (
      <TextField
        autoFocus
        size="small"
        fullWidth
        multiline
        maxRows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          setEditing(false);
          if (text !== short(value)) onChange(text);
        }}
        inputProps={{ "data-testid": `${testId}-input` }}
      />
    );
  return (
    <Typography
      typography="s2"
      onClick={() => setEditing(true)}
      sx={{ cursor: "text", whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      data-testid={testId}
    >
      {short(value)}
    </Typography>
  );
}
EditableCell.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  testId: PropTypes.string,
};

function Body({ request, onClose }) {
  const { datasetId, rowIds = [] } = request;
  const [form, setForm] = useState({
    n: 10,
    basis: rowIds.length ? "rows" : "all",
    description: "",
    keep: [],
    spread: "",
    instructions: "",
    model: "",
    kbId: "",
  });
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const { data: kbs = [] } = useKnowledgeBaseList("", {
    enabled: Boolean(datasetId),
  });
  const [drafts, setDrafts] = useState(null);
  const [ticked, setTicked] = useState(new Set());
  const [edits, setEdits] = useState({});
  const [attempts, setAttempts] = useState({});
  const [extra, setExtra] = useState(null);

  const asked = useDebounce(form, 400);
  const optionsQ = useQuery({
    queryKey: ["generate-rows-options", datasetId, asked, rowIds],
    queryFn: () =>
      axios
        .post(IMPORTS_PATH, {
          op: "preview",
          estimate_only: true,
          source: sourceOf(asked, rowIds),
          target: { dataset_id: datasetId },
        })
        .then((r) => r?.data?.result),
    enabled: Boolean(datasetId),
    staleTime: 30000,
    meta: { errorHandled: true },
  });
  const options = optionsQ.data;
  const columns = options?.columns ?? [];
  const inputCol = columns.find(
    (c) => c.role === "input" && c.data_type === "text",
  )?.name;
  const expectedCol = columns.find(
    (c) => c.role === "expected" && c.data_type === "text",
  )?.name;

  const previewM = useMutation({
    mutationFn: (att) =>
      axios
        .post(IMPORTS_PATH, {
          op: "preview",
          source: sourceOf(form, rowIds, att),
          target: { dataset_id: datasetId },
        })
        .then((r) => r?.data?.result),
    meta: { errorHandled: true },
  });
  const generate = () =>
    previewM.mutate(attempts, {
      onSuccess: (res) => {
        setDrafts(res.items);
        setExtra({
          coverage: res.coverage_text,
          cost: res.cost_usd,
          summary: res.summary,
        });
        setTicked(
          new Set(
            res.items
              .filter((it) => it.status === "new")
              .map((it) => it.temp_id),
          ),
        );
        setEdits({});
      },
    });
  const regenerate = (tempId) => {
    const next = { ...attempts, [tempId]: (attempts[tempId] ?? 0) + 1 };
    previewM.mutate(next, {
      onSuccess: (res) => {
        setAttempts(next);
        const fresh = res.items.find((it) => it.temp_id === tempId);
        setDrafts((ds) => ds.map((d) => (d.temp_id === tempId ? fresh : d)));
        setEdits(({ [tempId]: _drop, ...rest }) => rest);
        setTicked((t) => {
          const n = new Set(t);
          if (fresh.status === "new") n.add(tempId);
          else n.delete(tempId);
          return n;
        });
        setExtra({
          coverage: res.coverage_text,
          cost: res.cost_usd,
          summary: res.summary,
        });
      },
    });
  };

  const commitM = useMutation({
    mutationFn: () =>
      axios
        .post(IMPORTS_PATH, {
          op: "commit",
          source: sourceOf(form, rowIds, attempts),
          target: { dataset_id: datasetId },
          temp_ids: [...ticked],
          edits,
        })
        .then((r) => r?.data?.result),
    meta: { errorHandled: true },
    onSuccess: (res) => {
      const pending = toPendingOps(res.operations ?? [], {
        stamp: `gen-${Date.now().toString(36)}-`,
        hint: res.description_hint,
      });
      const cs = useChangeSetStore.getState();
      pending.forEach((op) => cs.addOp(datasetId, op));
      const ids = pending.map((o) => o.temp_id);
      revealNewRows(datasetId, ids);
      onClose();
      enqueueSnackbar(
        `${pending.length} ${pending.length === 1 ? "row" : "rows"} added to pending changes · Needs review`,
        {
          variant: "success",
          action: (key) => (
            <>
              <Button
                size="small"
                color="inherit"
                sx={{ fontWeight: 600 }}
                onClick={() => {
                  closeSnackbar(key);
                  useVersionDialogs.getState().openSave(datasetId);
                }}
              >
                Save version
              </Button>
              <RunOnRowsButton
                variant="toastAction"
                datasetId={datasetId}
                rowIds={ids}
                label="Re-test these rows"
                onDone={() => closeSnackbar(key)}
              />
            </>
          ),
        },
      );
    },
  });

  const estimateText = options?.estimate
    ? `${money(options.estimate.cost_usd)} · ${options.estimate.n} ${options.estimate.n === 1 ? "row" : "rows"} with ${options.estimate.model}`
    : null;
  const n = Number(form.n);
  const block =
    !Number.isInteger(n) || n < 1 || n > GENERATE_MAX
      ? `How many: 1 to ${GENERATE_MAX}`
      : form.basis === "description" && !form.description.trim()
        ? "Describe the rows to generate"
        : form.basis === "kb" && !form.kbId
          ? "Pick a knowledge base"
          : !form.model
            ? "Pick a model"
            : options?.problem ?? null;
  const addN = drafts ? drafts.filter((d) => ticked.has(d.temp_id)).length : 0;
  const shownCols = useMemo(
    () =>
      [
        ...new Set([
          ...(keepOffered(form.basis) ? form.keep ?? [] : []),
          ...(form.spread ? [form.spread] : []),
          ...(form.basis === "kb" ? ["citation"] : []),
        ]),
      ].filter((c) => c !== inputCol && c !== expectedCol),
    [form.keep, form.spread, form.basis, inputCol, expectedCol],
  );

  if (drafts)
    return (
      <>
        <DialogTitle>Generated rows</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={1}>
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="generate-summary"
            >
              {[
                `${drafts.length} drafts`,
                extra?.summary?.near_duplicates
                  ? `${extra.summary.near_duplicates} close to an existing row or draft (unticked)`
                  : null,
                extra?.summary?.invalid
                  ? `${extra.summary.invalid} don't match the schema`
                  : null,
                money(extra?.cost),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
            {extra?.coverage ? (
              <Typography typography="s2" data-testid="generate-coverage">
                {extra.coverage}
              </Typography>
            ) : null}
            <Stack spacing={0.75}>
              {drafts.map((d) => {
                const cells = { ...d.cells, ...(edits[d.temp_id] ?? {}) };
                const setCell = (col, value) =>
                  setEdits((e) => ({
                    ...e,
                    [d.temp_id]: { ...(e[d.temp_id] ?? {}), [col]: value },
                  }));
                return (
                  <Stack
                    key={d.temp_id}
                    direction="row"
                    gap={1}
                    alignItems="flex-start"
                    data-testid="generate-draft"
                    data-status={d.status}
                    sx={{
                      p: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      opacity: ticked.has(d.temp_id) ? 1 : 0.6,
                    }}
                  >
                    <Checkbox
                      size="small"
                      checked={ticked.has(d.temp_id)}
                      disabled={d.status === "invalid"}
                      onChange={(e) =>
                        setTicked((t) => {
                          const next = new Set(t);
                          if (e.target.checked) next.add(d.temp_id);
                          else next.delete(d.temp_id);
                          return next;
                        })
                      }
                      inputProps={{ "data-testid": "generate-tick" }}
                    />
                    <Stack spacing={0.5} sx={{ flex: 1, minWidth: 0 }}>
                      {inputCol ? (
                        <EditableCell
                          value={cells[inputCol]}
                          onChange={(v) => setCell(inputCol, v)}
                          testId="generate-input"
                        />
                      ) : null}
                      {expectedCol ? (
                        <Box sx={{ color: "text.secondary" }}>
                          <EditableCell
                            value={cells[expectedCol]}
                            onChange={(v) => setCell(expectedCol, v)}
                            testId="generate-expected"
                          />
                        </Box>
                      ) : null}
                      <Stack
                        direction="row"
                        gap={0.5}
                        flexWrap="wrap"
                        alignItems="center"
                      >
                        {d.near ? (
                          <Chip
                            size="small"
                            color="warning"
                            variant="outlined"
                            label={d.near.label}
                          />
                        ) : null}
                        {(d.issues ?? []).slice(0, 1).map((x) => (
                          <Chip
                            key={x.message}
                            size="small"
                            color="error"
                            variant="outlined"
                            label={x.message}
                          />
                        ))}
                        {shownCols.map((c) => (
                          <Chip
                            key={c}
                            size="small"
                            variant="outlined"
                            label={`${c}: ${short(cells[c])}`}
                          />
                        ))}
                        <Typography
                          typography="s3"
                          color="text.disabled"
                          data-testid="generate-note"
                        >
                          {d.note}
                        </Typography>
                      </Stack>
                    </Stack>
                    <CustomTooltip
                      show
                      title="Regenerate this row"
                      size="small"
                    >
                      <IconButton
                        size="small"
                        onClick={() => regenerate(d.temp_id)}
                        disabled={previewM.isPending}
                        data-testid="generate-regenerate"
                        aria-label="Regenerate this row"
                      >
                        <Iconify icon="mdi:refresh" width={16} />
                      </IconButton>
                    </CustomTooltip>
                  </Stack>
                );
              })}
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          {commitM.error ? (
            <Typography typography="s2" color="error.main" sx={{ flex: 1 }}>
              {getRequestErrorMessage(commitM.error, "Couldn't add the rows.")}
            </Typography>
          ) : null}
          <Button color="inherit" onClick={() => setDrafts(null)}>
            Back
          </Button>
          <LoadingButton
            variant="contained"
            disabled={!addN}
            loading={commitM.isPending}
            onClick={() => commitM.mutate()}
            data-testid="generate-add"
          >
            {`Add ${addN} ${addN === 1 ? "row" : "rows"} as pending changes`}
          </LoadingButton>
        </DialogActions>
      </>
    );

  return (
    <>
      <DialogTitle>Generate rows</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <TextField
            size="small"
            type="number"
            label="How many"
            value={form.n}
            onChange={(e) => set({ n: e.target.value })}
            helperText={`1 to ${GENERATE_MAX}`}
            inputProps={{
              min: 1,
              max: GENERATE_MAX,
              "data-testid": "generate-n",
            }}
            sx={{ width: 160 }}
          />
          <Box>
            <Typography typography="s2" color="text.secondary">
              Based on
            </Typography>
            <RadioGroup
              value={form.basis}
              onChange={(e) => set({ basis: e.target.value })}
            >
              {rowIds.length ? (
                <FormControlLabel
                  value="rows"
                  control={<Radio size="small" />}
                  label={`the ${rowIds.length} selected ${rowIds.length === 1 ? "row" : "rows"}`}
                />
              ) : null}
              <FormControlLabel
                value="all"
                control={<Radio size="small" />}
                label="every row"
              />
              <FormControlLabel
                value="description"
                control={<Radio size="small" />}
                label="a description"
              />
              {kbs.length ? (
                <FormControlLabel
                  value="kb"
                  control={<Radio size="small" />}
                  label="a knowledge base"
                />
              ) : null}
            </RadioGroup>
            {form.basis === "kb" ? (
              <TextField
                select
                size="small"
                fullWidth
                label="Knowledge base"
                value={form.kbId}
                onChange={(e) => set({ kbId: e.target.value })}
                helperText="One golden per passage: a question, the passage as the answer, its context and citation"
                SelectProps={{
                  SelectDisplayProps: { "data-testid": "generate-kb" },
                }}
              >
                {kbs.map((kb) => (
                  <MenuItem key={kb.id} value={kb.id}>
                    {kb.name}
                  </MenuItem>
                ))}
              </TextField>
            ) : null}
            {form.basis === "description" ? (
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={2}
                placeholder="Refund requests with partial shipments"
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
                inputProps={{ "data-testid": "generate-description" }}
              />
            ) : null}
          </Box>
          {/* v2.1 g8 (synthetic:generate-rows): Keep copies a field from
              the row a draft comes from, so it shows only when drafts come
              from rows (a description or a knowledge base has no row). */}
          {keepOffered(form.basis) ? (
            <Autocomplete
              multiple
              size="small"
              options={columns.map((c) => c.name)}
              value={form.keep}
              onChange={(_, v) => set({ keep: v })}
              renderInput={(params) => (
                <TextField {...params} label="Keep these fields as they are" />
              )}
              data-testid="generate-keep"
            />
          ) : null}
          <TextField
            select
            size="small"
            label="Spread across"
            value={form.spread}
            onChange={(e) => set({ spread: e.target.value })}
            SelectProps={{
              SelectDisplayProps: { "data-testid": "generate-spread" },
            }}
            helperText="Each draft takes the next value of this column"
          >
            <MenuItem value="">Nothing</MenuItem>
            {(options?.spreadable ?? []).map((c) => (
              <MenuItem key={c.name} value={c.name}>
                {`${c.name} (${c.values.slice(0, 5).join(", ")}${c.values.length > 5 ? ", …" : ""})`}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="Instructions"
            placeholder="edge cases, multilingual"
            value={form.instructions}
            onChange={(e) => set({ instructions: e.target.value })}
            helperText="The generator reads edge / hard / tricky (harder cases) and multilingual (every language)"
            inputProps={{ "data-testid": "generate-instructions" }}
          />
          <Box data-testid="generate-model">
            <CustomModelDropdown
              value={form.model}
              onChange={(e) =>
                set({ model: e?.target?.value?.model_name ?? "" })
              }
              buttonTitle="Model"
              searchDropdown
              size="small"
              fullWidth
              excludeCustomProviders
            />
          </Box>
          {estimateText ? (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="generate-estimate"
            >
              {estimateText}
            </Typography>
          ) : null}
          {form.model && options?.problem ? (
            <Typography
              typography="s2"
              color="error.main"
              data-testid="generate-problem"
            >
              {options.problem}
            </Typography>
          ) : null}
          {previewM.error ? (
            <Typography typography="s2" color="error.main">
              {getRequestErrorMessage(
                previewM.error,
                "Couldn't generate the rows.",
              )}
            </Typography>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <CustomTooltip show={Boolean(block)} title={block ?? ""} size="small">
          <span>
            <LoadingButton
              variant="contained"
              disabled={Boolean(block)}
              loading={previewM.isPending}
              onClick={generate}
              data-testid="generate-run"
            >
              Generate
            </LoadingButton>
          </span>
        </CustomTooltip>
      </DialogActions>
    </>
  );
}
Body.propTypes = {
  request: PropTypes.shape({
    datasetId: PropTypes.string.isRequired,
    rowIds: PropTypes.array,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
};

/** Mounted once on the dataset page; opens from the store. */
export default function GenerateRowsDialog() {
  const request = useGenerateRows((s) => s.request);
  const close = useGenerateRows((s) => s.close);
  const [busy, setBusy] = useState(false);
  const immediate = isLaunchBuild();
  const available =
    !immediate ||
    (request?.rowIds?.length
      ? isSurfaceShown("synthetic.extras")
      : isSurfaceShown("dataset.imports.generateRowsTile"));
  return (
    <Dialog
      aria-labelledby={immediate ? "immediate-generate-title" : undefined}
      open={Boolean(request) && available}
      onClose={busy ? undefined : close}
      disableEscapeKeyDown={busy}
      fullWidth
      maxWidth="sm"
      PaperProps={{
        sx: { maxWidth: 560 },
        "data-testid": "generate-rows-dialog",
      }}
    >
      {request && available ? (
        immediate ? (
          <ImmediateGenerateRows
            key={request.datasetId}
            request={request}
            onClose={close}
            onBusyChange={setBusy}
          />
        ) : (
          <Body request={request} onClose={close} />
        )
      ) : null}
    </Dialog>
  );
}
