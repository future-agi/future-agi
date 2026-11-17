// Row layouts (W9-4): `Copy “Refund review” to [dataset ▾]`. Fields and
// set-field buttons are matched by column name in the target; the dialog
// says what matched before and after the copy. The copy is a saved view in
// the target dataset's scope with the same sharing choice. A renderer
// someone else wrote keeps its author in the copy (renderer.author), so the
// copy asks before it runs there, for the copier too.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import { useSavedViews } from "src/api/develop/r3";
import { LAYOUT_SURFACE } from "./layoutModel";
import { copyLayoutTo, copyResultLine, targetColumns } from "./layoutMatch";
import { rendererAuthorOf, withRendererAuthor } from "./useRowLayouts";

const errText = (err) =>
  err?.response?.data?.result ??
  err?.response?.data?.message ??
  err?.message ??
  "The copy didn't save.";

const CopyLayoutDialog = ({ open, view, datasetId, viewer, onClose }) => {
  const [target, setTarget] = useState(null);
  const [name, setName] = useState(view?.name ?? "");
  const [done, setDone] = useState(null);
  const [error, setError] = useState(null);
  const list = useDevelopDatasetList("", undefined, { enabled: open });
  const options = useMemo(
    () =>
      (list.data ?? [])
        .filter((d) => d.datasetId && d.datasetId !== datasetId)
        .map((d) => ({ id: d.datasetId, name: d.name })),
    [list.data, datasetId],
  );
  const targetId = target?.id;
  const userCols = useGetDatasetColumns(targetId, {
    enabled: Boolean(targetId),
  });
  const allCols = useGetDatasetColumns(
    targetId,
    { enabled: Boolean(targetId) },
    { include_prompt: true },
  );
  const saved = useSavedViews(LAYOUT_SURFACE, targetId);
  const ready = Boolean(targetId) && userCols.isSuccess && allCols.isSuccess;
  const result = useMemo(
    () =>
      ready
        ? copyLayoutTo(
            view?.state?.layout,
            targetColumns(userCols.data ?? [], allCols.data ?? []),
          )
        : null,
    [ready, view, userCols.data, allCols.data],
  );
  const shared = view?.shared === true && !viewer;
  const author = rendererAuthorOf(view);
  const copy = async () => {
    setError(null);
    try {
      await saved.create(
        name.trim(),
        { layout: withRendererAuthor(result.spec, view) },
        { shared },
      );
      setDone(`Copied. ${copyResultLine(result, target.name)}`);
    } catch (err) {
      setError(errText(err));
    }
  };
  if (!view) return null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      data-testid="copy-layout-dialog"
    >
      <DialogTitle
        sx={{ typography: "m3" }}
      >{`Copy “${view.name}” to another dataset`}</DialogTitle>
      <DialogContent>
        <Stack gap={1.5} sx={{ pt: 0.5 }}>
          <Autocomplete
            size="small"
            options={options}
            loading={list.isLoading}
            value={target}
            onChange={(_, v) => {
              setTarget(v);
              setDone(null);
              setError(null);
            }}
            getOptionLabel={(o) => o.name ?? ""}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Dataset"
                inputProps={{
                  ...params.inputProps,
                  "data-testid": "copy-layout-target",
                }}
              />
            )}
            disabled={Boolean(done)}
          />
          <TextField
            size="small"
            label="Name in that dataset"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={Boolean(done)}
          />
          <Typography typography="s2" color="text.secondary">
            {shared
              ? "It is shared with everyone on that dataset, like this one."
              : "It is saved for you only, like this one."}
          </Typography>
          {author && result?.spec?.renderer && !done && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="copy-layout-author"
            >
              {`Its custom renderer was written by ${author}. In that dataset it runs only after each person agrees, you too.`}
            </Typography>
          )}
          {result && !done && (
            <Typography typography="s2" data-testid="copy-layout-preview">
              {copyResultLine(result, target.name)}
            </Typography>
          )}
          {result && result.shown === 0 && !done && (
            <Alert
              severity="warning"
              sx={{ typography: "s2" }}
              data-testid="copy-layout-empty"
            >
              {result.matched === 0
                ? "No field of this layout is a column there, so there is nothing to copy."
                : "None of the fields this layout shows is a column there; the copy would show only hidden fields."}
            </Alert>
          )}
          {error && (
            <Alert severity="error" sx={{ typography: "s2" }}>
              {error}
            </Alert>
          )}
          {done && (
            <Alert
              severity="success"
              sx={{ typography: "s2" }}
              data-testid="copy-layout-result"
            >
              {done}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" color="inherit" onClick={onClose}>
          {done ? "Close" : "Cancel"}
        </Button>
        {!done && (
          <Button
            size="small"
            variant="contained"
            disabled={
              !result || result.shown === 0 || !name.trim() || saved.isSaving
            }
            onClick={copy}
            data-testid="copy-layout-submit"
          >
            Copy layout
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

CopyLayoutDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  view: PropTypes.object,
  datasetId: PropTypes.string.isRequired,
  viewer: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};

export default CopyLayoutDialog;
