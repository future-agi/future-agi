import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { INPUT_SX, ROW_BOX_SX, T12 } from "../../drawerStyles";
import { Field, HintLine, MONO } from "../formBits";
import SampleTag from "./SampleTag";

const KEY_W = 210;
const MAX_FILE = 5 * 1024 * 1024;
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/* A name that holds a secret, whatever its value looks like. */
const SECRET_NAME =
  /(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|CREDENTIAL|PRIVATE|AUTH|DSN|DATABASE_URL|CONN)/i;
const isSecretName = (name) => SECRET_NAME.test(String(name || ""));

const shortcutSx = {
  ...T12,
  fontWeight: 600,
  color: "text.secondary",
  px: 0.75,
  minWidth: 0,
  height: 24,
  flexShrink: 0,
};

/* NAME=value lines from a pasted or uploaded .env; comments and blanks skipped. */
function parseEnv(raw) {
  const values = [];
  let invalid = 0;
  String(raw || "")
    .split(/\r?\n/)
    .forEach((line) => {
      const text = line.trim().replace(/^export\s+/, "");
      if (!text || text.startsWith("#")) return;
      const at = text.indexOf("=");
      const name = at > 0 ? text.slice(0, at).trim() : "";
      if (!NAME_RE.test(name)) {
        invalid += 1;
        return;
      }
      const value = text
        .slice(at + 1)
        .trim()
        .replace(/^(['"])(.*)\1$/, "$2");
      values.push({ name, value });
    });
  return { values, invalid };
}

/**
 * Environment values for a code agent, as rows: every name the agent's code
 * was found to read is a row to fill, a secret as a masked key. Pasting or
 * uploading a .env fills many at once; a credential file and the egress
 * allow-list sit underneath. `values` is `[{ name, value }]`, `files`
 * `[{ name }]`. `sample` marks the expected names as a sample scan's.
 */
export default function EnvValueRows({
  values,
  onValues,
  files,
  onFiles,
  expectedNames = [],
  egress,
  onEgress,
  sample = false,
}) {
  const [extraNames, setExtraNames] = useState([]);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState(null);
  const [fileError, setFileError] = useState(null);
  const fileRef = useRef(null);
  const envFileRef = useRef(null);

  const byName = new Map(values.map((v) => [v.name, v]));
  const names = [
    ...new Set([...expectedNames, ...values.map((v) => v.name), ...extraNames]),
  ];
  const missing = names.filter((n) => !byName.get(n)?.value);

  const setRow = (name, input) => {
    const rest = values.filter((v) => v.name !== name);
    onValues(input ? [...rest, { name, value: input }] : rest);
  };
  const removeRow = (name) => {
    onValues(values.filter((v) => v.name !== name));
    setExtraNames((list) => list.filter((n) => n !== name));
  };

  const takeEnv = (raw) => {
    const { values: parsed, invalid } = parseEnv(raw);
    if (parsed.length) {
      const taken = new Set(parsed.map((v) => v.name));
      onValues([...values.filter((v) => !taken.has(v.name)), ...parsed]);
      setExtraNames((list) => [
        ...new Set([
          ...list,
          ...parsed
            .map((v) => v.name)
            .filter((n) => !expectedNames.includes(n)),
        ]),
      ]);
    }
    const parts = [];
    if (parsed.length)
      parts.push(
        `Filled ${parsed.length} value${parsed.length === 1 ? "" : "s"}.`,
      );
    if (invalid)
      parts.push(
        `Skipped ${invalid} line${invalid === 1 ? "" : "s"} that ${invalid === 1 ? "isn't" : "aren't"} NAME=value.`,
      );
    setNote(
      parts.length
        ? { text: parts.join(" "), error: !parsed.length && invalid > 0 }
        : null,
    );
  };
  const onEnvFile = (file) => {
    if (!file) return;
    if (file.size > MAX_FILE) {
      setNote({ text: "A .env file up to 5 MiB.", error: true });
      return;
    }
    file
      .text()
      .then(takeEnv)
      .catch(() => setNote({ text: "Couldn't read that file.", error: true }));
  };
  // UI only: the file isn't uploaded anywhere; its name stands in for it.
  const onFile = (file) => {
    if (!file) return;
    if (!/\.json$/i.test(file.name)) {
      setFileError("Google service-account JSON only.");
      return;
    }
    if (file.size > MAX_FILE) {
      setFileError("Up to 5 MiB.");
      return;
    }
    setFileError(null);
    onFiles([
      ...files.filter((f) => f.name !== file.name),
      { name: file.name },
    ]);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ flex: 1, minWidth: 0 }}
        >
          <Typography sx={{ ...T12, fontWeight: 600 }}>
            Environment values
            {names.length > 0 && (
              <Box
                component="span"
                sx={{
                  fontWeight: 400,
                  color: "text.secondary",
                  ml: 0.75,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {missing.length
                  ? `${names.length - missing.length} of ${names.length} set`
                  : "all set"}
              </Box>
            )}
          </Typography>
          {sample && expectedNames.length > 0 && <SampleTag />}
        </Stack>
        <Button
          onClick={() => setPasting((o) => !o)}
          startIcon={<Iconify icon="solar:clipboard-text-linear" width={12} />}
          sx={shortcutSx}
        >
          Paste .env
        </Button>
        <Button
          onClick={() => envFileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={shortcutSx}
        >
          Upload .env
        </Button>
        <input
          ref={envFileRef}
          type="file"
          hidden
          accept=".env,text/plain"
          onChange={(e) => {
            onEnvFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </Stack>
      <Typography
        component="div"
        sx={{ ...T12, color: "text.secondary", mb: 1 }}
      >
        <HintLine
          text={
            expectedNames.length
              ? "What this agent's code reads. Test credentials only, never a production key."
              : "Test credentials and settings our sandbox runs your code with. Never a production key."
          }
          hint="Secrets resolve inside the sandbox and are never stored, logged or put in pull requests."
        />
      </Typography>

      {pasting && (
        <Box sx={{ mb: 1.25 }}>
          <TextField
            fullWidth
            multiline
            minRows={3}
            autoFocus
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setNote(null);
            }}
            placeholder={"OPENAI_API_KEY=…\nLOG_LEVEL=info"}
            inputProps={{
              spellCheck: false,
              autoComplete: "off",
              "aria-label": "Paste .env contents",
            }}
            sx={{
              ...INPUT_SX,
              "& .MuiInputBase-inputMultiline": { fontFamily: MONO, ...T12 },
            }}
          />
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{ mt: 0.75 }}
          >
            <Button
              onClick={() => {
                setPasting(false);
                setText("");
              }}
              sx={{ ...shortcutSx, height: 28 }}
            >
              Cancel
            </Button>
            <Button
              variant="outlined"
              disabled={!text.trim()}
              onClick={() => {
                takeEnv(text);
                setText("");
                setPasting(false);
              }}
              sx={{
                ...shortcutSx,
                height: 28,
                color: "text.primary",
                borderColor: "divider",
              }}
            >
              Fill values
            </Button>
          </Stack>
        </Box>
      )}
      {note && (
        <Typography
          role={note.error ? "alert" : undefined}
          sx={{
            ...T12,
            color: note.error ? "error.main" : "text.secondary",
            mb: 1,
          }}
        >
          {note.text}
        </Typography>
      )}

      {names.length > 0 ? (
        <Box sx={ROW_BOX_SX}>
          {names.map((name, i) => {
            const v = byName.get(name);
            const secret = isSecretName(name);
            const expected = expectedNames.includes(name);
            return (
              <Stack
                key={name}
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  px: 1.5,
                  py: 0.75,
                  borderTop: i ? "1px solid" : "none",
                  borderColor: "divider",
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  sx={{ width: KEY_W, flexShrink: 0, minWidth: 0 }}
                >
                  <Typography
                    noWrap
                    sx={{ ...T12, fontWeight: 600, fontFamily: MONO }}
                  >
                    {name}
                  </Typography>
                  {secret && (
                    <CustomTooltip
                      show
                      arrow
                      size="small"
                      title="A secret: stored encrypted, never shown again"
                    >
                      <Iconify
                        icon="solar:lock-keyhole-minimalistic-linear"
                        width={14}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                    </CustomTooltip>
                  )}
                </Stack>
                <Box flex={1} minWidth={0}>
                  <TextField
                    fullWidth
                    size="small"
                    type={secret ? "password" : "text"}
                    value={v?.value || ""}
                    onChange={(e) => setRow(name, e.target.value)}
                    placeholder={secret ? "Paste the key" : "Value"}
                    autoComplete={secret ? "new-password" : "off"}
                    inputProps={{ spellCheck: false, "aria-label": name }}
                    sx={INPUT_SX}
                  />
                </Box>
                <Typography
                  noWrap
                  sx={{
                    ...T12,
                    color: "text.secondary",
                    width: 64,
                    flexShrink: 0,
                    textAlign: "right",
                  }}
                >
                  {v?.value ? "" : "Not set"}
                </Typography>
                {expected ? (
                  <Box sx={{ width: 28, flexShrink: 0 }} />
                ) : (
                  <IconButton
                    size="small"
                    aria-label={`Remove ${name}`}
                    onClick={() => removeRow(name)}
                  >
                    <Iconify
                      icon="solar:trash-bin-trash-linear"
                      width={16}
                      sx={{ color: "text.secondary" }}
                    />
                  </IconButton>
                )}
              </Stack>
            );
          })}
        </Box>
      ) : (
        <Typography
          sx={{
            ...T12,
            color: "text.secondary",
            px: 1.5,
            py: 1,
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: "6px",
          }}
        >
          No values yet. Paste or upload a .env to add them.
        </Typography>
      )}

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
        <Typography
          sx={{
            ...T12,
            color: fileError ? "error.main" : "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {fileError ||
            "Credential file: Google service-account JSON only, up to 5 MiB."}
        </Typography>
        <Button
          onClick={() => fileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={shortcutSx}
        >
          Upload credential file
        </Button>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept="application/json,.json"
          onChange={(e) => {
            onFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </Stack>
      {files.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 0.75 }}>
          {files.map((f) => (
            <Stack
              key={f.name}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ ...ROW_BOX_SX, px: 1.25, py: 0.75 }}
            >
              <Iconify
                icon="solar:file-check-linear"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                noWrap
                sx={{ ...T12, fontFamily: MONO, flex: 1, minWidth: 0 }}
              >
                {f.name}
              </Typography>
              <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
                Mounted per run, never written to a run
              </Typography>
              <IconButton
                size="small"
                aria-label={`Remove ${f.name}`}
                onClick={() => onFiles(files.filter((x) => x.name !== f.name))}
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            </Stack>
          ))}
        </Stack>
      )}

      <Field
        label="Additional egress domains"
        mono
        value={egress}
        onChange={onEgress}
        placeholder="api.example.com, turn.example.com"
        helper="Optional. Public hostnames for hardcoded APIs or TURN endpoints. Everything else is firewalled while the agent runs."
        sx={{ mt: 1.5 }}
      />
    </Box>
  );
}
EnvValueRows.propTypes = {
  values: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, value: PropTypes.string }),
  ).isRequired,
  onValues: PropTypes.func.isRequired,
  files: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string }))
    .isRequired,
  onFiles: PropTypes.func.isRequired,
  expectedNames: PropTypes.arrayOf(PropTypes.string),
  egress: PropTypes.string,
  onEgress: PropTypes.func.isRequired,
  sample: PropTypes.bool,
};
