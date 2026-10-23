import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, IconButton, Stack, TextField, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { credentialFileRefOf, isSecretName, mergeEnvValues, parseEnvText } from "../../_mock/envValues";
import { defaultRefFor, keyRefOf } from "../../_mock/secretRefs";
import { MAX_EGRESS_DOMAINS, parseEgress } from "./preflight";
import SecretRefField from "./SecretRefField";
import { HintLine } from "./replicationBits";

const MONO = "ui-monospace, Menlo, monospace";
const KEY_W = 210;
const MAX_FILE = 5 * 1024 * 1024;

/**
 * Environment values for a code agent: the test credentials and settings our
 * sandbox runs its code with. Every name the agent was found to read is a
 * row to fill — a plain setting keeps its value, a secret takes a reference
 * into the team's secret manager (or a key, stored encrypted under one).
 * Values another agent from the same repository was given come in already
 * set, marked with where they came from, so only what's missing is asked.
 * Pasting or uploading a .env is the shortcut for filling many at once;
 * a Google service-account file and the sandbox's egress allow-list sit
 * underneath. Never folded away — a run can't start without these.
 *
 * Secrets are never stored as values — the same rule as Settings →
 * Environment variables.
 */
export default function EnvironmentValues({ values, onValues, files, onFiles, expectedNames = [], egress = null, onEgress = null }) {
  const domains = parseEgress(egress);
  /* What's typed in each row, by name — a typed key shows as typed while
     the record keeps only where it's held. */
  const [drafts, setDrafts] = useState({});
  /* Names a .env had with a raw secret: kept as rows so a reference can be added. */
  const [extraNames, setExtraNames] = useState([]);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState(null);
  const [fileError, setFileError] = useState(null);
  const fileRef = useRef(null);
  const envFileRef = useRef(null);

  const byName = new Map(values.map((v) => [v.name, v]));
  const names = [...new Set([...expectedNames, ...values.map((v) => v.name), ...extraNames])];
  const missing = names.filter((n) => !byName.has(n));
  const fromOthers = [...new Set(values.filter((v) => v.from).map((v) => v.from))];

  const setRow = (name, input) => {
    setDrafts((d) => ({ ...d, [name]: input }));
    const secret = isSecretName(name) || !!byName.get(name)?.secret;
    const trimmed = String(input || "").trim();
    const rest = values.filter((v) => v.name !== name);
    if (secret) {
      const ref = keyRefOf(trimmed, defaultRefFor(name));
      onValues(ref ? [...rest, { name, secret: true, ref }] : rest);
    } else {
      onValues(trimmed ? [...rest, { name, secret: false, value: trimmed }] : rest);
    }
  };
  const removeRow = (name) => {
    onValues(values.filter((v) => v.name !== name));
    setExtraNames((list) => list.filter((n) => n !== name));
    setDrafts((d) => { const next = { ...d }; delete next[name]; return next; });
  };

  /* Paste or upload: fills the rows it names; a raw secret is refused but its row stays. */
  const takeEnv = (raw) => {
    const { values: parsed, refused, invalid } = parseEnvText(raw);
    if (parsed.length) {
      onValues(mergeEnvValues(values, parsed));
      setDrafts((d) => { const next = { ...d }; parsed.forEach((v) => { delete next[v.name]; }); return next; });
    }
    if (refused.length) setExtraNames((list) => [...new Set([...list, ...refused])]);
    const parts = [];
    if (parsed.length) parts.push(`Filled ${parsed.length} value${parsed.length === 1 ? "" : "s"}.`);
    if (refused.length) parts.push(`${refused.join(", ")} ${refused.length === 1 ? "looks" : "look"} like ${refused.length === 1 ? "a secret" : "secrets"} — add where ${refused.length === 1 ? "it's" : "they're"} held (vault://…) in ${refused.length === 1 ? "its row" : "their rows"}.`);
    if (invalid.length) parts.push(`Skipped ${invalid.length} line${invalid.length === 1 ? "" : "s"} that ${invalid.length === 1 ? "isn't" : "aren't"} NAME=value.`);
    setNote(parts.length ? { text: parts.join(" "), error: refused.length > 0 || (!parsed.length && invalid.length > 0) } : null);
  };
  const addPasted = () => { takeEnv(text); setText(""); setPasting(false); };
  const onEnvFile = (file) => {
    if (!file) return;
    if (file.size > MAX_FILE) { setNote({ text: "A .env file up to 5 MiB.", error: true }); return; }
    file.text().then(takeEnv).catch(() => setNote({ text: "Couldn't read that file.", error: true }));
  };

  /* Never read into the browser: the file goes to our store and only its
     reference is kept; it's mounted per run. */
  const onFile = (file) => {
    if (!file) return;
    if (!/\.json$/i.test(file.name)) { setFileError("Google service-account JSON only."); return; }
    if (file.size > MAX_FILE) { setFileError("Up to 5 MiB."); return; }
    setFileError(null);
    onFiles([...files.filter((f) => f.name !== file.name), { name: file.name, ref: credentialFileRefOf(file.name) }]);
  };

  const shortcut = { typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.75, minWidth: 0, flexShrink: 0 };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ typography: "s3", fontWeight: 600, flex: 1, minWidth: 0 }}>
          Environment values
          {names.length > 0 && (
            <Box component="span" sx={{ fontWeight: 400, color: "text.subtitle", ml: 0.75, fontVariantNumeric: "tabular-nums" }}>
              {missing.length ? `${names.length - missing.length} of ${names.length} set` : "all set"}
            </Box>
          )}
        </Typography>
        <Button size="small" onClick={() => setPasting((o) => !o)} startIcon={<Iconify icon="solar:clipboard-text-linear" width={12} />} sx={shortcut}>
          Paste .env
        </Button>
        <Button size="small" onClick={() => envFileRef.current?.click()} startIcon={<Iconify icon="solar:upload-linear" width={12} />} sx={shortcut}>
          Upload .env
        </Button>
        <input ref={envFileRef} type="file" hidden accept=".env,text/plain" onChange={(e) => { onEnvFile(e.target.files?.[0]); e.target.value = ""; }} />
      </Stack>
      <Typography component="div" sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
        <HintLine hint="Secrets are references into your secret manager — the value is resolved inside the sandbox and never stored here, or written to runs, logs or pull requests. A key typed instead is stored encrypted under the reference shown.">
          {expectedNames.length
            ? "What this agent's code reads. Test credentials only — never a production key."
            : "Test credentials and settings our sandbox runs your code with — never a production key."}
        </HintLine>
      </Typography>

      {fromOthers.length > 0 && (
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1 }}>
          <Iconify icon="solar:history-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`Filled from ${fromOthers.join(", ")}, already connected from this repository${missing.length ? ` — ${missing.length === 1 ? "one value" : `${missing.length} values`} left to add.` : "."}`}
          </Typography>
        </Stack>
      )}

      {pasting && (
        <Box sx={{ mb: 1.25 }}>
          <TextField
            fullWidth multiline minRows={3} autoFocus
            value={text}
            onChange={(e) => { setText(e.target.value); setNote(null); }}
            placeholder={"OPENAI_API_KEY=vault://acme/sim/openai_api_key\nLOG_LEVEL=info"}
            inputProps={{ spellCheck: false, autoComplete: "off", "aria-label": "Paste .env contents" }}
            sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
          />
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 0.75 }}>
            <Button size="small" onClick={() => { setPasting(false); setText(""); }} sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
            <Button size="small" variant="outlined" disabled={!text.trim()} onClick={addPasted} sx={{ typography: "s3", fontWeight: 600, color: "text.primary", borderColor: "divider" }}>
              Fill values
            </Button>
          </Stack>
        </Box>
      )}
      {note && (
        <Typography role={note.error ? "alert" : undefined} sx={{ typography: "s3", color: note.error ? "error.main" : "text.subtitle", mb: 1 }}>
          {note.text}
        </Typography>
      )}

      {names.length > 0 ? (
        <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          {names.map((name, i) => {
            const v = byName.get(name);
            const secret = isSecretName(name) || !!v?.secret;
            const shown = drafts[name] ?? (v ? (v.secret ? v.ref : v.value) : "");
            const expected = expectedNames.includes(name);
            return (
              <Stack key={name} direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={{ xs: 0.5, sm: 1.5 }} sx={{ px: 1.5, py: 0.75, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                <Stack direction="row" alignItems="center" spacing={0.75} sx={{ width: { sm: KEY_W }, flexShrink: 0, minWidth: 0 }}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, minWidth: 0 }}>{name}</Typography>
                  {secret && (
                    <Tooltip arrow title="A secret — stored as a reference, the value stays in your secret manager">
                      <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                    </Tooltip>
                  )}
                </Stack>
                <Box flex={1} minWidth={0}>
                  {secret ? (
                    <SecretRefField dense fullWidth value={shown} onChange={(t) => setRow(name, t)} placeholder={defaultRefFor(name)} label={null} />
                  ) : (
                    <TextField
                      fullWidth size="small" value={shown} onChange={(e) => setRow(name, e.target.value)}
                      placeholder="Value"
                      inputProps={{ spellCheck: false, autoComplete: "off", "aria-label": name }}
                      sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
                    />
                  )}
                </Box>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", width: { sm: 120 }, flexShrink: 0, textAlign: { sm: "right" } }}>
                  {v?.from ? `from ${v.from}` : v ? "" : "Not set"}
                </Typography>
                {expected ? <Box sx={{ width: 28, flexShrink: 0, display: { xs: "none", sm: "block" } }} /> : (
                  <IconButton size="small" aria-label={`Remove ${name}`} onClick={() => removeRow(name)} sx={{ alignSelf: { xs: "flex-end", sm: "center" } }}>
                    <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                )}
              </Stack>
            );
          })}
        </Box>
      ) : (
        <Typography sx={{ typography: "s3", color: "text.subtitle", px: 1.5, py: 1, border: "1px dashed", borderColor: "divider", borderRadius: 1 }}>
          No values yet — paste or upload a .env to add them.
        </Typography>
      )}

      {/* A Google service-account file, mounted per run. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
        <Typography sx={{ typography: "s3", color: fileError ? "error.main" : "text.subtitle", flex: 1, minWidth: 0 }}>
          {fileError || "Credential file: Google service-account JSON only, up to 5 MiB."}
        </Typography>
        <Button size="small" onClick={() => fileRef.current?.click()} startIcon={<Iconify icon="solar:upload-linear" width={12} />} sx={shortcut}>
          Upload credential file
        </Button>
        <input ref={fileRef} type="file" hidden accept="application/json,.json" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
      </Stack>
      {files.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 0.75 }}>
          {files.map((f) => (
            <Stack key={f.name} direction="row" alignItems="center" spacing={1} sx={{ px: 1.25, py: 0.75, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
              <Iconify icon="solar:file-check-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
              <Typography noWrap sx={{ typography: "s2", fontFamily: MONO, flex: 1, minWidth: 0 }}>{f.name}</Typography>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>Mounted per run, never written to a run</Typography>
              <IconButton size="small" aria-label={`Remove ${f.name}`} onClick={() => onFiles(files.filter((x) => x.name !== f.name))}>
                <Iconify icon="eva:close-fill" width={15} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </Stack>
          ))}
        </Stack>
      )}

      {onEgress && (
        <Box sx={{ mt: 1.5 }}>
          <FieldLabel>Additional egress domains</FieldLabel>
          <TextField
            fullWidth size="small"
            value={egress || ""}
            onChange={(e) => onEgress(e.target.value)}
            placeholder="api.example.com, turn.example.com"
            inputProps={{ spellCheck: false, autoComplete: "off", "aria-label": "Additional egress domains" }}
            sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
          />
          <Typography sx={{ typography: "s3", color: domains.length > MAX_EGRESS_DOMAINS ? "error.main" : "text.subtitle", mt: 0.5 }}>
            {domains.length > MAX_EGRESS_DOMAINS
              ? `${domains.length} domains — the sandbox allows at most ${MAX_EGRESS_DOMAINS}.`
              : "Optional. Public hostnames for hardcoded APIs or TURN endpoints — everything else is firewalled while the agent runs."}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

/* A form field's label — the same as the connect forms' fields. Kept here so
   this file doesn't import the environment builder (an import cycle). */
function FieldLabel({ children }) {
  return <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>{children}</Typography>;
}
FieldLabel.propTypes = { children: PropTypes.node };

EnvironmentValues.propTypes = {
  /* `from` — the agent from the same repository a value was filled from. */
  values: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string, secret: PropTypes.bool, ref: PropTypes.string, value: PropTypes.string, from: PropTypes.string })).isRequired,
  onValues: PropTypes.func.isRequired,
  files: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string, ref: PropTypes.string })).isRequired,
  onFiles: PropTypes.func.isRequired,
  /* Names the agent's code is known to read — each a row to fill. */
  expectedNames: PropTypes.arrayOf(PropTypes.string),
  /* The egress allow-list as typed, and its setter — shown only when given. */
  egress: PropTypes.string,
  onEgress: PropTypes.func,
};
