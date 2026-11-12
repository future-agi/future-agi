// W7-4 remote scorers: Test on one row. A real call to the scorer with the
// request it would get in a run (signature shown, secret never), each try,
// the answer and the verdict the answer gives.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import {
  CALLED_FROM_BROWSER,
  testUrlMode,
  tryLine,
} from "src/sections/common/EvalTypes/remoteModel";
import { remoteOfForm } from "./remoteScorerForm";

const pre = {
  fontFamily: "monospace",
  fontSize: 12,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
  bgcolor: "background.neutral",
  borderRadius: 1,
  p: 1,
  m: 0,
};

// A signed scorer's secret is made when it is saved, so its Test waits.
const SAVE_FIRST =
  "Save the scorer first: Future AGI makes its signing secret when you save it.";

export default function RemoteTestPanel({ templateId, form, name }) {
  const unsavedSigned = !templateId && form.authKind === "hmac";
  const [source, setSource] = useState(templateId ? "row" : "typed");
  const [options, setOptions] = useState([]);
  const [datasetId, setDatasetId] = useState("");
  const [rowId, setRowId] = useState("");
  const [typed, setTyped] = useState({});
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const mode = testUrlMode(form.url);
  const inputNames = form.inputs.map((x) => x.name.trim()).filter(Boolean);

  useEffect(() => {
    if (!templateId) return undefined;
    let live = true;
    axios
      .get(`${endpoints.develop.w7.remote(templateId)}?for=test`)
      .then((res) => live && setOptions(res?.data?.result?.test_rows ?? []))
      .catch(() => live && setOptions([]));
    return () => {
      live = false;
    };
  }, [templateId]);

  const rows = useMemo(
    () => options.find((d) => d.dataset_id === datasetId)?.rows ?? [],
    [options, datasetId],
  );

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const remote = remoteOfForm(form);
      // A credential typed here is sent for this Test only, never stored.
      if (form.authKind === "bearer" && form.token)
        remote.auth.token = form.token;
      if (form.authKind === "oauth" && form.clientSecret)
        remote.auth.oauth = {
          ...remote.auth.oauth,
          client_secret: form.clientSecret,
        };
      const res = await axios.post(endpoints.develop.w7.remoteTest, {
        ...(templateId ? { template_id: templateId } : {}),
        name,
        remote,
        ...(source === "row" && datasetId && rowId
          ? { dataset_id: datasetId, row_id: rowId }
          : { inputs: typed }),
        mapping: remote.mapping,
        output,
        ...(form.passThreshold !== ""
          ? { pass_threshold: Number(form.passThreshold) }
          : {}),
      });
      setResult(res?.data?.result ?? null);
    } catch (e) {
      setError(e?.result ?? e?.message ?? "Test failed");
    } finally {
      setBusy(false);
    }
  };

  const v = result?.verdict;
  return (
    <Box
      data-testid="remote-test-panel"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 1 }}
      >
        <Typography variant="subtitle2">Test on one row</Typography>
        {mode.mode && (
          <Typography
            variant="caption"
            color="text.secondary"
            data-testid="remote-test-mode"
          >
            {mode.mode === "browser"
              ? CALLED_FROM_BROWSER
              : "Called by Future AGI, as in a run"}
          </Typography>
        )}
      </Stack>
      {templateId && (
        <ToggleButtonGroup
          size="small"
          exclusive
          value={source}
          onChange={(_, val) => val && setSource(val)}
          sx={{ mb: 1 }}
        >
          <ToggleButton value="row">A dataset row</ToggleButton>
          <ToggleButton value="typed">Type values</ToggleButton>
        </ToggleButtonGroup>
      )}
      {source === "row" && templateId ? (
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <TextField
            select
            size="small"
            label="Dataset"
            value={datasetId}
            onChange={(e) => {
              setDatasetId(e.target.value);
              setRowId("");
            }}
            sx={{ minWidth: 220 }}
            inputProps={{ "data-testid": "remote-test-dataset" }}
          >
            {options.map((d) => (
              <MenuItem key={d.dataset_id} value={d.dataset_id}>
                {d.dataset_name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Row"
            value={rowId}
            onChange={(e) => setRowId(e.target.value)}
            sx={{ minWidth: 260 }}
            inputProps={{ "data-testid": "remote-test-row" }}
          >
            {rows.map((r) => (
              <MenuItem
                key={r.id}
                value={r.id}
                data-testid={`remote-test-row-${r.label.replace(/\s+/g, "-")}`}
              >
                {r.label} · {r.preview}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      ) : (
        <Stack spacing={1} sx={{ mb: 1 }}>
          {inputNames.map((n) => (
            <TextField
              key={n}
              size="small"
              label={n}
              value={typed[n] ?? ""}
              onChange={(e) => setTyped((t) => ({ ...t, [n]: e.target.value }))}
            />
          ))}
        </Stack>
      )}
      <TextField
        fullWidth
        multiline
        minRows={2}
        size="small"
        label="Output to score"
        value={output}
        onChange={(e) => setOutput(e.target.value)}
        inputProps={{ "data-testid": "remote-test-output" }}
        sx={{ mb: 1 }}
      />
      {mode.error && (
        <Alert
          severity="warning"
          sx={{ mb: 1 }}
          data-testid="remote-test-url-error"
        >
          {mode.error}
        </Alert>
      )}
      {unsavedSigned && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="div"
          sx={{ mb: 1 }}
          data-testid="remote-test-save-first"
        >
          {SAVE_FIRST}
        </Typography>
      )}
      <Button
        variant="outlined"
        size="small"
        onClick={run}
        disabled={
          busy ||
          unsavedSigned ||
          Boolean(mode.error) ||
          (source === "row" && templateId && !rowId)
        }
        data-testid="remote-test-run"
      >
        {busy ? "Calling…" : "Test on one row"}
      </Button>
      {error && (
        <Alert severity="error" sx={{ mt: 1 }} data-testid="remote-test-error">
          {String(error)}
        </Alert>
      )}
      {result && (
        <Stack spacing={1} sx={{ mt: 1.5 }} data-testid="remote-test-result">
          <Typography
            variant="body2"
            data-testid="remote-test-verdict"
            color={v?.error ? "error.main" : "text.primary"}
            sx={{ fontWeight: 600 }}
          >
            {v?.error
              ? v.error
              : `${v.passed ? "Pass" : "Fail"} · ${v.label ?? Number(v.value).toFixed(2)}${v.reason ? ` · ${v.reason}` : ""}`}
          </Typography>
          <Box data-testid="remote-test-tries">
            {(result.tries ?? []).map((t) => (
              <Typography key={t.n} variant="caption" component="div">
                {tryLine(t)}
                {t.error && t.status ? ` · ${t.error}` : ""}
              </Typography>
            ))}
          </Box>
          <Typography variant="caption" color="text.secondary">
            Request · POST {result.request?.url}
          </Typography>
          <Box component="pre" sx={pre} data-testid="remote-test-headers">
            {Object.entries(result.request?.headers ?? {})
              .map(([k, val]) => `${k}: ${val}`)
              .join("\n")}
          </Box>
          <Box component="pre" sx={pre}>
            {JSON.stringify(result.request?.body, null, 2)}
          </Box>
          {result.response && (
            <>
              <Typography variant="caption" color="text.secondary">
                Answer · {result.response.status} ·{" "}
                {Math.round(result.response.ms)} ms
              </Typography>
              <Box component="pre" sx={pre} data-testid="remote-test-response">
                {typeof result.response.body === "string"
                  ? result.response.body
                  : JSON.stringify(result.response.body, null, 2)}
              </Box>
            </>
          )}
        </Stack>
      )}
    </Box>
  );
}

RemoteTestPanel.propTypes = {
  templateId: PropTypes.string,
  form: PropTypes.object.isRequired,
  name: PropTypes.string,
};
