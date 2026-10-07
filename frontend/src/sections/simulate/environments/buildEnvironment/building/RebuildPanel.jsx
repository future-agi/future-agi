import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Stack,
  Typography,
  Button,
  Chip,
  TextField,
  Radio,
  RadioGroup,
  FormControlLabel,
} from "@mui/material";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import { useRebuildEnvironment } from "src/api/simulate-environments/environments";
import { uploadHarnessSecretFile } from "src/api/harness/harness";
import { envVarGroups } from "../../workspace/settings/envVarGroups";
import { CREDENTIAL_FILE_ENV } from "../../workspace/settings/configurationEdit";
import {
  KindChip,
  RowAction,
  VariableRow,
} from "../../workspace/settings/configurationParts";
import {
  MASK,
  dividerSx,
  inlineInputSx,
  valueSx,
} from "../../workspace/settings/configurationStyles";
import {
  CHOICE_LABEL,
  buildRebuildBody,
  isFileAlias,
  optionLabel,
  requiredAliases,
} from "./rebuildInputs";

const PEN = "solar:pen-2-linear";
const CLOSE = "solar:close-circle-linear";

export default function RebuildPanel({ envId, inputNeeded = null }) {
  const rebuild = useRebuildEnvironment();
  const detailQuery = useQuery(harnessEnvironmentQuery(envId));
  const secrets = envVarGroups(detailQuery.data?.settings?.agent).secrets;
  const [picks, setPicks] = useState({});
  const [values, setValues] = useState({});
  const [files, setFiles] = useState({});
  const [uploading, setUploading] = useState(null);
  const [checks, setChecks] = useState([]);
  const [error, setError] = useState(null);
  const fileInput = useRef(null);
  const uploadFor = useRef(null);

  const asksForInput = Boolean(
    inputNeeded?.keys?.length || inputNeeded?.choices?.length,
  );
  const required = requiredAliases(inputNeeded, picks);
  const { body, missing } = buildRebuildBody({ required, values, files });
  const canRebuild = !missing.length && !rebuild.isPending && !uploading;
  const failedChecks = checks.filter((check) => check.status === "failed");

  const setValue = (alias, value) =>
    setValues((current) => ({ ...current, [alias]: value }));

  const toggleReplace = (alias) =>
    setValues((current) => {
      const next = { ...current };
      if (alias in next) delete next[alias];
      else next[alias] = "";
      return next;
    });

  const pickFile = (alias) => {
    uploadFor.current = alias;
    fileInput.current?.click();
  };

  const upload = async (file) => {
    const alias = uploadFor.current;
    if (!file || !alias) return;
    setError(null);
    setUploading(alias);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("environment_name", CREDENTIAL_FILE_ENV);
      const result = await uploadHarnessSecretFile(form);
      setFiles((current) => ({ ...current, [alias]: result?.secret_ref }));
    } catch (e) {
      setError(e?.detail || e?.message || "Couldn't upload the file.");
    } finally {
      setUploading(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const submit = () => {
    if (!canRebuild) return;
    setError(null);
    setChecks([]);
    rebuild.mutate(
      { id: envId, body },
      {
        onError: (e) => {
          setChecks(e?.checks || []);
          setError(e?.detail || e?.message || "Couldn't start the rebuild.");
        },
      },
    );
  };

  const inputRow = (alias) => {
    if (isFileAlias(alias)) {
      const label = files[alias] ? `Replace ${alias}` : `Upload ${alias}`;
      return (
        <VariableRow
          key={alias}
          name={alias}
          chip={<KindChip label="file" />}
          first={
            <RowAction
              icon={files[alias] ? PEN : "solar:upload-linear"}
              label={label}
              onClick={() => pickFile(alias)}
            />
          }
        >
          <Typography sx={valueSx}>
            {uploading === alias
              ? "Uploading…"
              : files[alias]
                ? "Uploaded"
                : "Not uploaded"}
          </Typography>
        </VariableRow>
      );
    }
    return (
      <VariableRow key={alias} name={alias} chip={<KindChip label="secret" />}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <TextField
            size="small"
            type="password"
            autoComplete="off"
            placeholder="Value"
            inputProps={{ "aria-label": `Value for ${alias}` }}
            value={values[alias] || ""}
            onChange={(e) => setValue(alias, e.target.value)}
            sx={{ width: "100%", maxWidth: 460, ...inlineInputSx }}
          />
        </Box>
      </VariableRow>
    );
  };

  return (
    <Box
      sx={{
        mx: 2.5,
        mb: 3,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        hidden
        data-testid="rebuild-file-input"
        onChange={(e) => upload(e.target.files?.[0])}
      />
      <Box sx={{ px: 2.5, py: 2, ...dividerSx }}>
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          Fix and rebuild
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {asksForInput
            ? "The build stopped because these values are missing. Add them and rebuild."
            : "Please verify these values. Replace any that are wrong, then rebuild."}
        </Typography>
      </Box>

      <Stack divider={<Box sx={dividerSx} />}>
        {(inputNeeded?.keys || []).map(inputRow)}

        {(inputNeeded?.choices || []).map((choice) => (
          <Box key={choice.id}>
            <Box sx={{ px: 2.5, pt: 1.5 }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {CHOICE_LABEL[choice.id] || choice.id}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Provide one of these
              </Typography>
              <RadioGroup
                row
                aria-label={CHOICE_LABEL[choice.id] || choice.id}
                value={String(picks[choice.id] ?? 0)}
                onChange={(e) =>
                  setPicks((current) => ({
                    ...current,
                    [choice.id]: Number(e.target.value),
                  }))
                }
              >
                {choice.options.map((option, index) => (
                  <FormControlLabel
                    key={optionLabel(option)}
                    value={String(index)}
                    control={<Radio size="small" />}
                    label={optionLabel(option)}
                    sx={{
                      "& .MuiFormControlLabel-label": { typography: "s3" },
                    }}
                  />
                ))}
              </RadioGroup>
            </Box>
            <Stack divider={<Box sx={dividerSx} />}>
              {(choice.options[picks[choice.id] ?? 0] || []).map(inputRow)}
            </Stack>
          </Box>
        ))}

        {!asksForInput &&
          secrets.map((alias) => {
            const editing = alias in values;
            return (
              <VariableRow
                key={alias}
                name={alias}
                chip={<KindChip label="secret" />}
                first={
                  <RowAction
                    icon={editing ? CLOSE : PEN}
                    label={
                      editing ? `Keep current ${alias}` : `Replace ${alias}`
                    }
                    onClick={() => toggleReplace(alias)}
                  />
                }
              >
                {editing ? (
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <TextField
                      size="small"
                      type="password"
                      autoComplete="off"
                      placeholder="New value"
                      inputProps={{ "aria-label": `New value for ${alias}` }}
                      value={values[alias]}
                      onChange={(e) => setValue(alias, e.target.value)}
                      sx={{ width: "100%", maxWidth: 460, ...inlineInputSx }}
                    />
                  </Box>
                ) : (
                  <Typography noWrap sx={valueSx}>
                    {MASK}
                  </Typography>
                )}
              </VariableRow>
            );
          })}
      </Stack>

      <Stack
        spacing={1}
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Button
            variant="contained"
            size="small"
            onClick={submit}
            disabled={!canRebuild}
          >
            {rebuild.isPending ? "Checking…" : "Rebuild"}
          </Button>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Everything is checked again before the build starts.
          </Typography>
        </Stack>
        {error && (
          <Typography
            role="alert"
            sx={{ typography: "s2", color: "error.main" }}
          >
            {error}
          </Typography>
        )}
        {failedChecks.map((check) => (
          <Stack key={check.id} spacing={0.25}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Chip size="small" color="error" label="Failed" />
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {check.label}
              </Typography>
            </Stack>
            <Typography sx={{ typography: "s3" }}>{check.detail}</Typography>
            {check.fix && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {check.fix}
              </Typography>
            )}
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

RebuildPanel.propTypes = {
  envId: PropTypes.string.isRequired,
  inputNeeded: PropTypes.shape({
    keys: PropTypes.arrayOf(PropTypes.string),
    choices: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        options: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.string)),
      }),
    ),
  }),
};
