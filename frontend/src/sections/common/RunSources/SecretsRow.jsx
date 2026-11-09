// Model API keys (R4-SPEC D21; options-truth L1 G2). Where the run's model
// keys come from is a real choice: Workspace secrets (a set / missing check
// per key, `Add` saves the name through POST /model-hub/secrets/), This
// machine's environment, or Paste for this run (kept in the draft only).
// The keys follow the run's models by vendor. Code with no declared model
// asks for its agent's model key (L3 G4 C2: the model the sample agent
// calls, else "your agent's model key"), never "no keys"; CI lists the
// repository secrets the workflow reads, each with `gh secret set`.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { vendorKeysOf } from "./declaredUses";

/** Key names a run needs from its model ids, one per vendor (pure). */
export const keysForModels = (models = []) =>
  vendorKeysOf(models.map((m) => m?.name ?? m?.id ?? m));

export const SECRET_SOURCES = Object.freeze([
  { id: "workspace", label: "Workspace secrets" },
  { id: "machine", label: "This machine's environment" },
  { id: "paste", label: "Paste for this run" },
]);

/** The source a mode starts on: machine for local runs, else workspace. */
export const defaultSecretSource = (mode) =>
  mode === "local" || mode === "connect" ? "machine" : "workspace";

const mono = { fontFamily: "monospace" };

function AddSecretDialog({ name, open, onClose, onAdded }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [value, setValue] = useState("");
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // the name is saved; the value stays in the vault (never returned)
      await axios.post(endpoints.secrets.create, {
        name,
        secret_type: "api_key",
        description: `Model key for experiment runs (${name})`,
      });
      onAdded(name);
      onClose();
    } catch (e) {
      setError(getRequestErrorMessage(e, `Couldn't add ${name}.`));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle typography="m3">Add {name}</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ pt: 0.5 }}>
          <Typography typography="s2" color="text.secondary">
            Saved as a workspace secret. Runs in the sandbox and in CI read it
            by name.
          </Typography>
          <TextField
            size="small"
            type="password"
            label={name}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            inputProps={{ "data-testid": "secret-value" }}
          />
          {error ? (
            <Typography typography="s2" color="error.main">
              {error}
            </Typography>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          disabled={busy || !value.trim()}
          onClick={save}
          data-testid="secret-save"
        >
          Save secret
        </Button>
      </DialogActions>
    </Dialog>
  );
}
AddSecretDialog.propTypes = {
  name: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAdded: PropTypes.func,
};

/**
 * The key rows of a run (pure, L3 G4 C2): the declared models' keys; for
 * code with none, the keys of the models its agent calls (`agentModels`,
 * suffixed `(your agent's model)`), else one generic line. Never empty
 * for code.
 * @returns {{keys: string[], note: string|null, agentKey: boolean}}
 */
export function keyRowsOf({ kind, models = [], agentModels = [] } = {}) {
  const keys = keysForModels(models ?? []);
  if (keys.length || kind !== "code")
    return { keys, note: null, agentKey: false };
  const own = keysForModels(agentModels ?? []);
  if (own.length) return { keys: own, note: null, agentKey: true };
  return {
    keys: [],
    note: "Your agent's model key: set it where the run starts",
    agentKey: true,
  };
}

export default function SecretsRow({
  mode,
  kind,
  models,
  agentModels,
  secrets,
  source,
  onSource,
}) {
  const [own, setOwn] = useState(null);
  const src = source ?? own ?? defaultSecretSource(mode);
  const setSrc = (v) => {
    setOwn(v);
    onSource?.(v);
  };
  const [adding, setAdding] = useState(null);
  const [added, setAdded] = useState([]);
  const [pasted, setPasted] = useState({});
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["secrets", "r4-names"],
    queryFn: () => axios.get(endpoints.secrets.list, { params: { page: 1 } }),
    select: (res) => (res?.data?.results ?? []).map((s) => s.name),
    enabled: src === "workspace",
    staleTime: 60_000,
  });
  const rows = keyRowsOf({ kind, models, agentModels });
  const needed = rows.keys;
  const tag = rows.agentKey ? " (your agent's model)" : "";
  const have = new Set([...(data ?? []), ...added]);
  const head = (
    <Stack direction="row" gap={0.75} alignItems="center">
      <Iconify
        icon="mdi:key-outline"
        width={16}
        sx={{ color: "text.secondary" }}
      />
      <Typography typography="s2">Model API keys come from:</Typography>
      <Select
        size="small"
        value={src}
        onChange={(e) => setSrc(e.target.value)}
        sx={{ height: 28, typography: "s2" }}
        inputProps={{ "aria-label": "Model API keys come from" }}
        data-testid="secrets-source"
      >
        {SECRET_SOURCES.map((s) => (
          <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
            {s.label}
          </MenuItem>
        ))}
      </Select>
    </Stack>
  );
  let body;
  if (mode === "ci" && secrets?.length)
    // the repository secrets the workflow reads, each with its command
    return (
      <Stack spacing={0.5} data-testid="secrets-row">
        <Typography typography="s2">
          Repository secrets the workflow reads:
        </Typography>
        {secrets.map((k) => (
          <Typography
            key={k}
            typography="s2"
            sx={{ ...mono, pl: 3 }}
            data-testid={`secret-${k}`}
          >
            {`gh secret set ${k}`}
          </Typography>
        ))}
      </Stack>
    );
  if (!needed.length)
    body = (
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="secret-note"
      >
        {rows.note ?? "No model keys needed"}
      </Typography>
    );
  else if (src === "machine")
    body = (
      <Typography typography="s2" color="text.secondary">
        Read from the environment where the run starts:{" "}
        <Typography component="span" typography="s2" sx={mono}>
          {needed.join(", ")}
        </Typography>
        {tag}
      </Typography>
    );
  else if (src === "paste")
    body = needed.map((k) => (
      <TextField
        key={k}
        size="small"
        type="password"
        label={`${k}${tag}`}
        value={pasted[k] ?? ""}
        onChange={(e) => setPasted({ ...pasted, [k]: e.target.value })}
        helperText={pasted[k] ? "✓ for this run only, never saved" : " "}
        sx={{ width: 260 }}
      />
    ));
  else
    body = needed.map((k) =>
      have.has(k) ? (
        <Typography
          key={k}
          typography="s2"
          sx={mono}
          data-testid={`secret-${k}`}
        >
          {k}
          {tag}{" "}
          <Typography component="span" typography="s2" color="success.main">
            ✓ set
          </Typography>
        </Typography>
      ) : (
        <Stack key={k} direction="row" alignItems="center" gap={0.5}>
          <Typography typography="s2" sx={mono} data-testid={`secret-${k}`}>
            {k}
            {tag}{" "}
            <Typography component="span" typography="s2" color="error.main">
              ✗ missing
            </Typography>
          </Typography>
          <Button
            size="small"
            onClick={() => setAdding(k)}
            sx={{ minWidth: 0, px: 0.5, typography: "s2" }}
            data-testid={`secret-add-${k}`}
          >
            Add
          </Button>
        </Stack>
      ),
    );
  return (
    <Stack spacing={0.75} data-testid="secrets-row">
      {head}
      <Stack direction="row" flexWrap="wrap" gap={1.5} sx={{ pl: 3 }}>
        {body}
      </Stack>
      {adding ? (
        <AddSecretDialog
          name={adding}
          open
          onClose={() => setAdding(null)}
          onAdded={(name) => {
            setAdded((a) => [...a, name]);
            qc.invalidateQueries({ queryKey: ["secrets", "r4-names"] });
          }}
        />
      ) : null}
    </Stack>
  );
}

SecretsRow.propTypes = {
  mode: PropTypes.string,
  /** `code`: never "No model keys needed" (agentModels / generic line). */
  kind: PropTypes.string,
  models: PropTypes.array,
  /** The models the code's agent calls (its sample) when none is declared. */
  agentModels: PropTypes.array,
  /** CI: the repository secrets the workflow reads (`gh secret set`). */
  secrets: PropTypes.array,
  source: PropTypes.string,
  onSource: PropTypes.func,
};
