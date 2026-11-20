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
import { parseDotEnv } from "src/pages/dashboard/harness/dotenv";
import { T12 } from "../drawerStyles";
import DrawerEnvValues from "./DrawerEnvValues";
import { HintLine, MONO } from "./formBits";

const KEY_W = 210;
const MAX_FILE = 5 * 1024 * 1024;
const SECRET_NAME = /(KEY|SECRET|TOKEN|PASSWORD|PASS|DSN|CREDENTIAL|AUTH)/i;
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

const shortcutSx = {
  ...T12,
  fontWeight: 600,
  color: "text.secondary",
  px: 0.75,
  minWidth: 0,
  flexShrink: 0,
};

const monoInputSx = {
  "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO },
};

export default function CodeEnvValues({
  names,
  values,
  onValues,
  extra,
  onExtra,
  egress,
  onEgress,
  secretFiles,
  onSecretFiles,
}) {
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [draftName, setDraftName] = useState("");
  const [note, setNote] = useState(null);
  const envFileRef = useRef(null);

  const rows = [...names, ...extra.filter((n) => !names.includes(n))];
  const filled = rows.filter((n) => String(values[n] ?? "").trim()).length;

  const takeEnv = (raw) => {
    let parsed;
    try {
      parsed = parseDotEnv(raw);
    } catch (e) {
      setNote({ text: e.message, error: true });
      return;
    }
    const entries = Object.entries(parsed).filter(([, v]) => v !== "");
    if (!entries.length) {
      setNote({ text: "No NAME=value lines found.", error: true });
      return;
    }
    onValues({ ...values, ...Object.fromEntries(entries) });
    const added = entries.map(([n]) => n).filter((n) => !rows.includes(n));
    if (added.length) onExtra([...extra, ...added]);
    setNote({
      text: `Filled ${entries.length} value${entries.length === 1 ? "" : "s"}.`,
      error: false,
    });
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

  const addName = () => {
    const name = draftName.trim().toUpperCase();
    if (!NAME.test(name) || rows.includes(name)) return;
    onExtra([...extra, name]);
    setDraftName("");
  };

  const removeRow = (name) => {
    onExtra(extra.filter((n) => n !== name));
    const next = { ...values };
    delete next[name];
    onValues(next);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ ...T12, fontWeight: 600, flex: 1, minWidth: 0 }}>
          Environment values
          {rows.length > 0 && (
            <Box
              component="span"
              sx={{
                fontWeight: 400,
                color: "text.secondary",
                ml: 0.75,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {`${filled} of ${rows.length} set`}
            </Box>
          )}
        </Typography>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setPasting((open) => !open)}
          startIcon={<Iconify icon="solar:clipboard-text-linear" width={12} />}
          sx={shortcutSx}
        >
          Paste .env
        </Button>
        <Button
          variant="outlined"
          size="small"
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
            names.length
              ? "What this agent's code reads. Test credentials only, never a production key."
              : "Test credentials and settings our sandbox runs your code with. Never a production key."
          }
          hint="Values are sent only for preflight and runs, and never written to jobs, logs or artifacts."
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
            placeholder={"ANTHROPIC_API_KEY=…\nLOG_LEVEL=info"}
            inputProps={{
              spellCheck: false,
              autoComplete: "off",
              "aria-label": "Paste .env contents",
            }}
            sx={monoInputSx}
          />
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{ mt: 0.75 }}
          >
            <Button
              size="small"
              color="inherit"
              onClick={() => {
                setPasting(false);
                setText("");
              }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={!text.trim()}
              onClick={() => {
                takeEnv(text);
                setText("");
                setPasting(false);
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

      {rows.length > 0 && (
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          {rows.map((name, i) => {
            const secret = SECRET_NAME.test(name);
            const scanned = names.includes(name);
            return (
              <Stack
                key={name}
                direction={{ xs: "column", sm: "row" }}
                alignItems={{ sm: "center" }}
                spacing={{ xs: 0.5, sm: 1.5 }}
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
                  sx={{ width: { sm: KEY_W }, flexShrink: 0, minWidth: 0 }}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontFamily: MONO,
                      minWidth: 0,
                    }}
                  >
                    {name}
                  </Typography>
                  {secret && (
                    <CustomTooltip
                      show
                      arrow
                      size="small"
                      title="A secret: sent write-only and never shown again"
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
                    value={values[name] ?? ""}
                    onChange={(e) =>
                      onValues({ ...values, [name]: e.target.value })
                    }
                    placeholder={secret ? "Paste the key" : "Value"}
                    autoComplete={secret ? "new-password" : "off"}
                    inputProps={{
                      spellCheck: false,
                      "aria-label": name,
                      "data-testid": `env-value-${name}`,
                    }}
                    sx={monoInputSx}
                  />
                </Box>
                {scanned ? (
                  <Box
                    sx={{
                      width: 28,
                      flexShrink: 0,
                      display: { xs: "none", sm: "block" },
                    }}
                  />
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
      )}

      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
        <TextField
          size="small"
          placeholder="ANOTHER_NAME"
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addName();
            }
          }}
          inputProps={{
            spellCheck: false,
            "aria-label": "Another name",
            "data-testid": "env-name-draft",
          }}
          sx={{ maxWidth: 260, ...monoInputSx }}
        />
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={addName}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
        >
          Add a value
        </Button>
      </Stack>

      {onEgress && onSecretFiles && (
        <Box sx={{ mt: 1.5 }}>
          <DrawerEnvValues
            bare
            envText=""
            onEnvText={() => {}}
            egress={egress}
            onEgress={onEgress}
            secretFiles={secretFiles}
            onSecretFiles={onSecretFiles}
          />
        </Box>
      )}
    </Box>
  );
}

CodeEnvValues.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
  values: PropTypes.object.isRequired,
  onValues: PropTypes.func.isRequired,
  extra: PropTypes.arrayOf(PropTypes.string).isRequired,
  onExtra: PropTypes.func.isRequired,
  egress: PropTypes.string,
  onEgress: PropTypes.func,
  secretFiles: PropTypes.array,
  onSecretFiles: PropTypes.func,
};
