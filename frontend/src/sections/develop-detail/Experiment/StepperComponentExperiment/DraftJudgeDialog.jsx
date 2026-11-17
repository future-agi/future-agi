// W1-2 (LS-01): "Draft a judge from <expected column>". Reads the column on
// every row of the dataset version the experiment runs on, lists the kinds
// of facts it states with counts, and drafts an LLM judge from the facts the
// author ticks, labelled as a rule-based draft. The author edits its name
// and instructions before saving; Create saves a real eval template
// (eval-templates/create-v2) with the edited text. The new eval joins the
// experiment's evaluations; the run scores it like any custom eval (the one
// scorer reads the saved instructions).
import React, { useEffect, useMemo, useState } from "react";
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
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { tableRequestFor } from "src/sections/develop-detail/DataTab/Versions/versionedSource";
import {
  draftFactsLine,
  draftFactsOf,
  draftJudgeOf,
  draftNameOf,
} from "./draftJudgeModel";

// The dataset table's largest page (the reader pages through the rest).
const PAGE_SIZE = 500;

// The column's value on every row of the version the experiment runs on
// (latest when none is pinned).
async function columnValuesOf(datasetId, columnId, version) {
  const values = [];
  for (let page = 0; ; page += 1) {
    const { url, params } = tableRequestFor({
      datasetId,
      versionNumber: version?.number ?? null,
      versionId: version?.id ?? null,
      params: { page_size: PAGE_SIZE, current_page_index: page },
    });
    // eslint-disable-next-line no-await-in-loop
    const res = await axios.get(url, { params });
    const result = res?.data?.result ?? {};
    (result.table ?? []).forEach((row) =>
      values.push(row?.[columnId]?.cell_value ?? null),
    );
    const meta = result.metadata ?? {};
    if (!meta.has_more || !(result.table ?? []).length) break;
  }
  return values;
}

const DraftJudgeDialog = ({
  open,
  onClose,
  datasetId,
  column,
  version = null,
  onCreated,
}) => {
  const [values, setValues] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState([]);
  const [saving, setSaving] = useState(false);
  // The author's own words; null follows the draft (the ticked facts).
  const [nameEdit, setNameEdit] = useState(null);
  const [textEdit, setTextEdit] = useState(null);
  useEffect(() => {
    if (!open || !datasetId || !column?.id) return undefined;
    let live = true;
    setValues(null);
    setError(null);
    setPicked([]);
    setNameEdit(null);
    setTextEdit(null);
    columnValuesOf(datasetId, column.id, version)
      .then((v) => live && setValues(v))
      .catch(
        (e) =>
          live &&
          setError(getRequestErrorMessage(e, "Could not read the column")),
      );
    return () => {
      live = false;
    };
  }, [open, datasetId, column?.id, version]);
  const facts = useMemo(() => (values ? draftFactsOf(values) : []), [values]);
  const draft = draftJudgeOf({
    column: column?.name,
    picked: facts.filter((f) => picked.includes(f.key)),
  });
  const displayName = nameEdit ?? draft.displayName;
  const instructions = textEdit ?? draft.instructions;
  const missing = !displayName.trim()
    ? "Give the judge a name"
    : !instructions.trim()
      ? "Write what the judge checks"
      : null;

  const create = async () => {
    setSaving(true);
    setError(null);
    // The template's name follows the name people see.
    const stem = nameEdit != null ? draftNameOf(displayName, "") : draft.name;
    try {
      // A second draft of the same column gets the next free name.
      let name = stem;
      let res = null;
      for (let i = 1; i <= 20; i += 1) {
        name = i === 1 ? stem : `${stem}_${i}`;
        try {
          // eslint-disable-next-line no-await-in-loop
          res = await axios.post(endpoints.develop.eval.createEvalTemplateV2, {
            name,
            eval_type: "llm",
            instructions: instructions.trim(),
            description:
              textEdit != null
                ? `${displayName.trim()}: ${instructions.trim()}`
                : draft.description,
            output_type: "pass_fail",
            tags: ["draft"],
          });
          break;
        } catch (e) {
          if (!/already exists/i.test(getRequestErrorMessage(e, ""))) throw e;
        }
      }
      const id = res?.data?.result?.id;
      if (!id) throw new Error("The draft judge was not created");
      onCreated?.({
        templateId: id,
        name,
        displayName: displayName.trim(),
        mapping: { output: "output", expected: column.name },
        requiredKeys: ["output", "expected"],
        config: { criteria: instructions.trim() },
        evalTemplate: {
          output_type: "Pass/Fail",
          requiredKeys: ["output", "expected"],
        },
      });
      onClose?.();
    } catch (e) {
      setError(getRequestErrorMessage(e, "Could not create the draft judge"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={saving ? undefined : onClose}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>{`Draft a judge from ${column?.name ?? "the expected column"}`}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} data-testid="draft-judge-dialog">
          <Typography typography="s2" color="text.secondary">
            {draft.label}
          </Typography>
          {!values && !error ? (
            <Typography typography="s2">
              {`Reading ${column?.name} on every row…`}
            </Typography>
          ) : null}
          {values ? (
            <Typography typography="s2" data-testid="draft-facts-line">
              {facts.length
                ? `Found ${draftFactsLine(facts)}. Tick what the reply must state.`
                : `No ids, amounts, dates or time frames in ${column?.name}. The draft checks that the reply gives the same answer.`}
            </Typography>
          ) : null}
          <Box>
            {facts.map((f) => (
              <FormControlLabel
                key={f.key}
                control={
                  <Checkbox
                    size="small"
                    checked={picked.includes(f.key)}
                    onChange={(e) =>
                      setPicked((cur) =>
                        e.target.checked
                          ? [...cur, f.key]
                          : cur.filter((k) => k !== f.key),
                      )
                    }
                  />
                }
                label={`${f.label} · in ${f.rows} of ${f.total} rows`}
                data-testid={`draft-fact-${f.key}`}
              />
            ))}
          </Box>
          {values ? (
            <Stack spacing={1.5}>
              <TextField
                size="small"
                label="Name"
                value={displayName}
                onChange={(e) => setNameEdit(e.target.value)}
                helperText="LLM judge · Pass/Fail · saved as a draft eval"
                inputProps={{ "data-testid": "draft-name" }}
              />
              <TextField
                size="small"
                label="Instructions"
                value={instructions}
                onChange={(e) => setTextEdit(e.target.value)}
                multiline
                minRows={3}
                helperText={
                  textEdit == null
                    ? "Follows the facts you tick until you edit it. {{output}} is the run's reply, {{expected}} the column."
                    : "Your words are saved as the judge's instructions."
                }
                inputProps={{ "data-testid": "draft-instructions" }}
              />
            </Stack>
          ) : null}
          {error ? (
            <Typography typography="s2" color="error.main">
              {error}
            </Typography>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          loading={saving}
          disabled={!values || Boolean(missing)}
          title={missing ?? undefined}
          onClick={create}
          data-testid="draft-judge-create"
        >
          Create draft judge
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DraftJudgeDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  column: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  version: PropTypes.shape({ id: PropTypes.string, number: PropTypes.number }),
  onCreated: PropTypes.func,
};

export default DraftJudgeDialog;
