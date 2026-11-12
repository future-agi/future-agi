// Metadata… (W9-SCOPE §5 A1): one dataset edits its key = value rows or
// **Edit as YAML**; several datasets get **Set or change keys** rows and
// **Remove keys** chips. **Save metadata** sends one PUT per dataset, in
// sequence (api/develop/dataset-org.js); the result toast names a partial
// failure. Rules and sentences: orgRules.js via metadataModel.js.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { sequenceToast, useSaveDatasetOrg } from "src/api/develop/dataset-org";
import {
  bulkCopy,
  bulkPatch,
  fromYaml,
  fullPatch,
  keysAcross,
  mapFromRows,
  rowsOf,
  toYaml,
  valueInput,
} from "./metadataModel";

const blank = () => ({ key: "", value: "" });
const asInputs = (metadata) =>
  rowsOf(metadata).map((r) => ({ key: r.key, value: valueInput(r.value) }));

export function MetadataRows({ rows, onChange, idPrefix }) {
  const set = (i, patch) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Stack gap={1}>
      {rows.map((r, i) => (
        <Stack
          key={`${idPrefix}-${i}`}
          direction="row"
          gap={1}
          alignItems="center"
        >
          <TextField
            size="small"
            placeholder="key"
            value={r.key}
            onChange={(e) => set(i, { key: e.target.value })}
            inputProps={{ "aria-label": `Key ${i + 1}` }}
            sx={{ width: 180 }}
          />
          <Typography typography="s2" color="text.secondary">
            =
          </Typography>
          <TextField
            size="small"
            placeholder="value"
            value={r.value}
            onChange={(e) => set(i, { value: e.target.value })}
            inputProps={{ "aria-label": `Value ${i + 1}` }}
            sx={{ flex: 1 }}
          />
          <IconButton
            size="small"
            aria-label={`Remove ${r.key || `row ${i + 1}`}`}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
          >
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </Stack>
      ))}
      <Box>
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() => onChange([...rows, blank()])}
        >
          Add
        </Button>
      </Box>
    </Stack>
  );
}
MetadataRows.propTypes = {
  rows: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  idPrefix: PropTypes.string,
};

/**
 * @param {{id: string, name: string, metadata?: object}[]} props.datasets
 * @param {(toast: {variant, message}) => void} props.onDone
 */
export default function MetadataDialog({ open, datasets, onClose, onDone }) {
  const single = datasets?.length === 1;
  const before = single ? datasets[0].metadata ?? {} : null;
  const [rows, setRows] = useState([]);
  const [yaml, setYaml] = useState(null); // text while editing as YAML
  const [removeKeys, setRemoveKeys] = useState([]);
  const [error, setError] = useState(null);
  const save = useSaveDatasetOrg();
  useEffect(() => {
    if (!open) return;
    setRows(single ? [...asInputs(before), blank()] : [blank()]);
    setYaml(null);
    setRemoveKeys([]);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const across = useMemo(() => keysAcross(datasets), [datasets]);

  const toggleYaml = () => {
    if (yaml === null) {
      const { metadata, error: e } = mapFromRows(rows);
      if (e) return setError(e);
      setError(null);
      return setYaml(toYaml(metadata));
    }
    const { metadata, error: e } = fromYaml(yaml);
    if (e) return setError(e);
    setError(null);
    setRows([...asInputs(metadata), blank()]);
    return setYaml(null);
  };

  const submit = async () => {
    let patch;
    if (single) {
      const parsed = yaml === null ? mapFromRows(rows) : fromYaml(yaml);
      if (parsed.error) return setError(parsed.error);
      patch = fullPatch(before, parsed.metadata);
    } else {
      const res = bulkPatch(rows, removeKeys);
      if (res.error) return setError(res.error);
      patch = res.patch;
    }
    setError(null);
    const result = await save.mutateAsync({
      datasets,
      patchFor: () => ({ metadata: patch }),
    });
    const toast = sequenceToast(result, "Saved metadata on");
    if (!result.saved.length && result.failed.length) {
      setError(single ? result.failed[0].error : toast.message);
      return undefined;
    }
    onDone?.(toast);
    onClose();
    return undefined;
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pb: 1 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {single
            ? `Metadata of ${datasets[0].name}`
            : `Metadata of ${datasets?.length ?? 0} datasets`}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {single
            ? "Key = value pairs you can filter and group the list by."
            : bulkCopy(datasets?.length ?? 0)}
        </Typography>
      </DialogTitle>
      <DialogContent>
        {!single && (
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            sx={{ mb: 1 }}
          >
            Set or change keys
          </Typography>
        )}
        {yaml === null ? (
          <MetadataRows rows={rows} onChange={setRows} idPrefix="meta" />
        ) : (
          <TextField
            multiline
            fullWidth
            minRows={6}
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            inputProps={{ "aria-label": "Metadata as YAML", spellCheck: false }}
            sx={{ "& textarea": { fontFamily: "monospace", fontSize: 13 } }}
          />
        )}
        {!single && across.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              sx={{ mb: 1 }}
            >
              Remove keys
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              {across.map(({ key, count }) => {
                const on = removeKeys.includes(key);
                return (
                  <Chip
                    key={key}
                    size="small"
                    label={`${key} · ${count}`}
                    color={on ? "error" : "default"}
                    variant={on ? "filled" : "outlined"}
                    aria-pressed={on}
                    onClick={() =>
                      setRemoveKeys(
                        on
                          ? removeKeys.filter((k) => k !== key)
                          : [...removeKeys, key],
                      )
                    }
                  />
                );
              })}
            </Stack>
          </Box>
        )}
        {error && (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1.5 }}
            role="alert"
          >
            {error}
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5, justifyContent: "space-between" }}>
        {single ? (
          <Button size="small" color="inherit" onClick={toggleYaml}>
            {yaml === null ? "Edit as YAML" : "Edit as rows"}
          </Button>
        ) : (
          <span />
        )}
        <Stack direction="row" gap={1}>
          <Button variant="outlined" color="inherit" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={submit}
            disabled={save.isPending}
          >
            Save metadata
          </Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}

MetadataDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      metadata: PropTypes.object,
    }),
  ).isRequired,
  onClose: PropTypes.func.isRequired,
  onDone: PropTypes.func,
};
