import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Button, IconButton, Stack, TextField, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { credentialFileRefOf, mergeEnvValues, parseEnvText } from "../../_mock/envValues";
import { MAX_EGRESS_DOMAINS, parseEgress } from "./preflight";

const MONO = "ui-monospace, Menlo, monospace";
const KEY_W = 210;
const MAX_FILE = 5 * 1024 * 1024;

/**
 * Environment values for a code agent, as in the product's repo and upload
 * panels: the test credentials and settings our sandbox runs the code with —
 * .env values, a credential file, and the public hostnames the sandbox may
 * reach while the agent runs (hardcoded APIs, TURN endpoints — everything
 * else is blocked). Test credentials only, never a production key. Optional
 * and folded until asked for; the credential file stays reachable while
 * it's folded, since a source that needs one must never hide it behind
 * "optional".
 *
 * Secrets go in as references into the team's secret manager (vault://…),
 * never the value — the same rule as Settings → Environment variables.
 */
export default function EnvironmentValues({ values, onValues, files, onFiles, expectedNames = [], egress = null, onEgress = null }) {
  const [open, setOpen] = useState(values.length > 0 || !!egress);
  const domains = parseEgress(egress);
  const [text, setText] = useState("");
  const [note, setNote] = useState(null);
  const [fileError, setFileError] = useState(null);
  const fileRef = useRef(null);

  const addPasted = () => {
    const { values: parsed, refused, invalid } = parseEnvText(text);
    if (parsed.length) onValues(mergeEnvValues(values, parsed));
    const parts = [];
    if (parsed.length) parts.push(`Added ${parsed.length} value${parsed.length === 1 ? "" : "s"}.`);
    if (refused.length) parts.push(`${refused.join(", ")} ${refused.length === 1 ? "looks" : "look"} like ${refused.length === 1 ? "a secret" : "secrets"} — paste where ${refused.length === 1 ? "it's" : "they're"} held (vault://…), not the value.`);
    if (invalid.length) parts.push(`Skipped ${invalid.length} line${invalid.length === 1 ? "" : "s"} that ${invalid.length === 1 ? "isn't" : "aren't"} NAME=value.`);
    setNote(parts.length ? { text: parts.join(" "), error: refused.length > 0 || (!parsed.length && invalid.length > 0) } : null);
    /* Keep only the refused lines, so the person can fix them in place. */
    setText(refused.length ? refused.map((n) => `${n}=vault://`).join("\n") : "");
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

  const missing = expectedNames.filter((n) => !values.some((v) => v.name === n));

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Button
          size="small"
          onClick={() => setOpen((o) => !o)}
          startIcon={<Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
          sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5 }}
        >
          {open ? "Hide environment values" : `Environment values (optional)${values.length + domains.length ? ` · ${values.length + domains.length}` : ""}`}
        </Button>
        <Button
          size="small"
          onClick={() => fileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={{ typography: "s3", fontWeight: 600, color: "text.secondary", px: 0.5, flexShrink: 0 }}
        >
          Upload credential file
        </Button>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept="application/json,.json"
          onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }}
        />
      </Stack>
      <Typography sx={{ typography: "s3", color: fileError ? "error.main" : "text.subtitle", px: 0.5 }}>
        {fileError || "Test credential file: Google service-account JSON only, up to 5 MiB."}
      </Typography>

      {files.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 1 }}>
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

      {open && (
        <Stack spacing={1.5} sx={{ mt: 1.25 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Test credentials and settings our sandbox runs your code with — never a production key. Secrets are references into your secret manager; the value is resolved inside the sandbox and never stored here, or written to runs, logs or pull requests.
          </Typography>

          {values.length > 0 && (
            <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
              {values.map((v, i) => (
                <Stack key={v.name} direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.5, py: 0.875, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: MONO, width: KEY_W, flexShrink: 0 }}>{v.name}</Typography>
                  <Typography noWrap sx={{ typography: "s2", fontFamily: MONO, color: "text.secondary", flex: 1, minWidth: 0 }}>
                    {v.secret ? v.ref : v.value || "—"}
                  </Typography>
                  {v.secret && (
                    <Tooltip arrow title="Stored as a reference — the value stays in your secret manager">
                      <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                    </Tooltip>
                  )}
                  <IconButton size="small" aria-label={`Remove ${v.name}`} onClick={() => onValues(values.filter((x) => x.name !== v.name))}>
                    <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Stack>
              ))}
            </Box>
          )}

          <Box>
            <FieldLabel>Paste .env contents</FieldLabel>
            <TextField
              fullWidth
              multiline
              minRows={3}
              value={text}
              onChange={(e) => { setText(e.target.value); setNote(null); }}
              placeholder={"OPENAI_API_KEY=vault://acme/sim/openai_api_key\nLOG_LEVEL=info"}
              inputProps={{ spellCheck: false, autoComplete: "off", "aria-label": "Paste .env contents" }}
              sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
            />
            <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ mt: 0.75 }}>
              <Typography role={note?.error ? "alert" : undefined} sx={{ typography: "s3", color: note?.error ? "error.main" : "text.subtitle", flex: 1 }}>
                {note?.text || (missing.length
                  ? `The code reads ${missing.join(", ")}. Plain settings keep their value; secrets take a reference.`
                  : "One NAME=value per line. Plain settings keep their value; secrets take a reference.")}
              </Typography>
              <Button size="small" variant="outlined" disabled={!text.trim()} onClick={addPasted} sx={{ typography: "s3", fontWeight: 600, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
                Add values
              </Button>
            </Stack>
          </Box>

          {onEgress && (
            <Box>
              <FieldLabel>Additional egress domains</FieldLabel>
              <TextField
                fullWidth
                value={egress || ""}
                onChange={(e) => onEgress(e.target.value)}
                placeholder="api.example.com, turn.example.com"
                inputProps={{ spellCheck: false, autoComplete: "off", "aria-label": "Additional egress domains" }}
                sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO } }}
              />
              <Typography sx={{ typography: "s3", color: domains.length > MAX_EGRESS_DOMAINS ? "error.main" : "text.subtitle", mt: 0.5 }}>
                {domains.length > MAX_EGRESS_DOMAINS
                  ? `${domains.length} domains — the sandbox allows at most ${MAX_EGRESS_DOMAINS}.`
                  : "Comma or newline-separated public hostnames for hardcoded APIs or TURN endpoints. Everything else is firewalled at the sandbox while the agent runs; the build itself doesn't use this list."}
              </Typography>
            </Box>
          )}
        </Stack>
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
  values: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string, secret: PropTypes.bool, ref: PropTypes.string, value: PropTypes.string })).isRequired,
  onValues: PropTypes.func.isRequired,
  files: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string, ref: PropTypes.string })).isRequired,
  onFiles: PropTypes.func.isRequired,
  /* Names the code is known to read (.env.example), when they're known. */
  expectedNames: PropTypes.arrayOf(PropTypes.string),
  /* The egress allow-list as typed, and its setter — shown only when given. */
  egress: PropTypes.string,
  onEgress: PropTypes.func,
};
