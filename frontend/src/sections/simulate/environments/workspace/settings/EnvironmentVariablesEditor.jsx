import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  Switch,
} from "@mui/material";
import { useUpdateEnvironmentConfiguration } from "src/api/simulate-environments/environments";
import SectionCard from "../../components/SectionCard";
import {
  SPEAKS_FIRST_FIELD,
  VARIABLES_FIELD,
  buildConfigurationBody,
  displayConfigValue,
  needsRebuild,
  ownConfigKeys,
  textToVariables,
  validateKeyName,
  variablesToText,
} from "./configurationEdit";
import {
  KindChip,
  RebuildOnlyAction,
  RowAction,
  SaveFooter,
  ValueText,
  VariableRow,
} from "./configurationParts";
import {
  MASK,
  dividerSx,
  compactInputSx,
  inlineInputSx,
  monoInputSx,
  outlinedButtonSx,
  valueSx,
} from "./configurationStyles";

const PEN = "solar:pen-2-linear";
const CLOSE = "solar:close-circle-linear";

export default function EnvironmentVariablesEditor({
  envId,
  groups,
  connector,
}) {
  const update = useUpdateEnvironmentConfiguration();
  const config = groups.config;
  const [secretDrafts, setSecretDrafts] = useState({});
  const [addedSecrets, setAddedSecrets] = useState({});
  const [connectionDrafts, setConnectionDrafts] = useState({});
  const [speaksFirst, setSpeaksFirst] = useState(undefined);
  const [variablesText, setVariablesText] = useState(() =>
    variablesToText(config[VARIABLES_FIELD]),
  );
  const [draft, setDraft] = useState({ key: "", value: "", secret: true });
  const [draftError, setDraftError] = useState(null);
  const [checks, setChecks] = useState([]);
  const [error, setError] = useState(null);

  const parsed = textToVariables(variablesText, config[VARIABLES_FIELD]);
  const body = buildConfigurationBody({
    config,
    secretDrafts,
    addedSecrets,
    connectionDrafts,
    speaksFirst,
    variables: parsed.variables,
  });
  const canSave = Boolean(body) && !parsed.error && !update.isPending;

  const reset = (environment) => {
    setSecretDrafts({});
    setAddedSecrets({});
    setConnectionDrafts({});
    setSpeaksFirst(undefined);
    setVariablesText(
      variablesToText(environment?.settings?.agent?.config?.[VARIABLES_FIELD]),
    );
  };

  const save = () => {
    if (!canSave) return;
    setError(null);
    setChecks([]);
    update.mutate(
      { id: envId, body },
      {
        onSuccess: (data) => {
          setChecks(data?.checks || []);
          reset(data?.environment);
        },
        onError: (e) => {
          setChecks(e?.checks || []);
          setError(e?.detail || e?.message || "Couldn't save the changes.");
        },
      },
    );
  };

  const addDraft = () => {
    if (draft.secret) {
      const name = draft.key.trim().toUpperCase();
      const problem =
        validateKeyName(name, [
          ...groups.secrets,
          ...Object.keys(addedSecrets),
        ]) || (draft.value.trim() ? null : "Enter a value.");
      if (problem) {
        setDraftError(problem);
        return;
      }
      setAddedSecrets((added) => ({ ...added, [name]: draft.value }));
    } else {
      const name = draft.key.trim();
      if (name.includes("=")) {
        setDraftError("A name can't contain =.");
        return;
      }
      if (parsed.variables && name in parsed.variables) {
        setDraftError(`${name} is already set. Edit it in the box below.`);
        return;
      }
      setVariablesText((text) =>
        [text.replace(/\n+$/, ""), `${name}=${draft.value.trim()}`]
          .filter(Boolean)
          .join("\n"),
      );
    }
    setDraftError(null);
    setDraft((d) => ({ key: "", value: "", secret: d.secret }));
  };

  const toggle = (setter, key, initial) =>
    setter((drafts) => {
      const next = { ...drafts };
      if (key in next) delete next[key];
      else next[key] = initial;
      return next;
    });

  const removeAdded = (alias) =>
    setAddedSecrets((added) => {
      const next = { ...added };
      delete next[alias];
      return next;
    });

  const configRow = (key) => {
    if (key === SPEAKS_FIRST_FIELD) {
      return (
        <VariableRow key={key} name={key} chip={<KindChip label="config" />}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ flex: 1, minWidth: 0 }}
          >
            <Switch
              size="small"
              checked={speaksFirst ?? Boolean(config[SPEAKS_FIRST_FIELD])}
              inputProps={{ "aria-label": "Agent always speaks first" }}
              onChange={(e) => setSpeaksFirst(e.target.checked)}
            />
            <Typography sx={{ typography: "s2" }}>
              Agent always speaks first
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Off: follows the call direction
            </Typography>
          </Stack>
        </VariableRow>
      );
    }
    if (needsRebuild(key)) {
      return (
        <VariableRow
          key={key}
          name={key}
          chip={<KindChip label="config" />}
          first={<RebuildOnlyAction name={key} />}
        >
          <ValueText text={displayConfigValue(config[key])} />
        </VariableRow>
      );
    }
    const editing = key in connectionDrafts;
    return (
      <VariableRow
        key={key}
        name={key}
        chip={<KindChip label="config" />}
        first={
          <RowAction
            icon={editing ? CLOSE : PEN}
            label={editing ? `Keep current ${key}` : `Edit ${key}`}
            onClick={() =>
              toggle(setConnectionDrafts, key, String(config[key] ?? ""))
            }
          />
        }
      >
        {editing ? (
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <TextField
              size="small"
              inputProps={{ "aria-label": key }}
              value={connectionDrafts[key]}
              onChange={(e) =>
                setConnectionDrafts((drafts) => ({
                  ...drafts,
                  [key]: e.target.value,
                }))
              }
              sx={{ width: "100%", maxWidth: 460, ...inlineInputSx }}
            />
          </Box>
        ) : (
          <ValueText text={displayConfigValue(config[key])} />
        )}
      </VariableRow>
    );
  };

  return (
    <SectionCard
      title="Environment variables"
      subtitle="Runtime secrets and configuration passed into the environment"
    >
      <Stack divider={<Box sx={dividerSx} />}>
        {groups.secrets.map((alias) => {
          const editing = alias in secretDrafts;
          return (
            <VariableRow
              key={`secret:${alias}`}
              name={alias}
              chip={<KindChip label="secret" />}
              first={
                <RowAction
                  icon={editing ? CLOSE : PEN}
                  label={editing ? `Keep current ${alias}` : `Replace ${alias}`}
                  onClick={() => toggle(setSecretDrafts, alias, "")}
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
                    value={secretDrafts[alias]}
                    onChange={(e) =>
                      setSecretDrafts((drafts) => ({
                        ...drafts,
                        [alias]: e.target.value,
                      }))
                    }
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

        {Object.keys(addedSecrets).map((alias) => (
          <VariableRow
            key={`added:${alias}`}
            name={alias}
            chip={<KindChip label="new secret" />}
            second={
              <RowAction
                icon="solar:trash-bin-trash-linear"
                label={`Don't add ${alias}`}
                onClick={() => removeAdded(alias)}
              />
            }
          >
            <Typography noWrap sx={valueSx}>
              {MASK}
            </Typography>
          </VariableRow>
        ))}

        {ownConfigKeys(config, connector).map(configRow)}
      </Stack>

      <Box
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <TextField
            size="small"
            placeholder="ENV_KEY_…"
            inputProps={{ "aria-label": "New key name" }}
            value={draft.key}
            onChange={(e) => {
              setDraftError(null);
              setDraft((d) => ({
                ...d,
                key: d.secret ? e.target.value.toUpperCase() : e.target.value,
              }));
            }}
            error={Boolean(draftError)}
            sx={{ width: 220, ...compactInputSx }}
          />
          <TextField
            size="small"
            type={draft.secret ? "password" : "text"}
            autoComplete="off"
            placeholder="Value"
            inputProps={{ "aria-label": "New key value" }}
            value={draft.value}
            onChange={(e) => setDraft((d) => ({ ...d, value: e.target.value }))}
            sx={{ flex: 1, maxWidth: 360, ...compactInputSx }}
          />
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Switch
              size="small"
              checked={draft.secret}
              inputProps={{ "aria-label": "Secret" }}
              onChange={(e) => {
                setDraftError(null);
                setDraft((d) => ({ ...d, secret: e.target.checked }));
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Secret
            </Typography>
          </Stack>
          <Button
            variant="outlined"
            size="small"
            onClick={addDraft}
            disabled={!draft.key.trim()}
            sx={{ ...outlinedButtonSx, height: 32 }}
          >
            Add
          </Button>
        </Stack>
        {draftError && (
          <Typography sx={{ typography: "s3", color: "error.main", mt: 0.75 }}>
            {draftError}
          </Typography>
        )}
      </Box>

      <Box
        sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Dynamic variables
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
          Your own values passed to the agent on each call. One per line:
          KEY=value
        </Typography>
        <TextField
          multiline
          fullWidth
          minRows={3}
          placeholder={"customer_name=Alex\ntier=gold"}
          inputProps={{ "aria-label": "Dynamic variables" }}
          value={variablesText}
          onChange={(e) => setVariablesText(e.target.value)}
          error={Boolean(parsed.error)}
          helperText={parsed.error || undefined}
          sx={monoInputSx}
        />
      </Box>

      <SaveFooter
        canSave={canSave}
        pending={update.isPending}
        onSave={save}
        hint="Changed keys are checked with their provider before saving. Changes apply to the next run."
        error={error}
        checks={checks}
      />
    </SectionCard>
  );
}

EnvironmentVariablesEditor.propTypes = {
  envId: PropTypes.string.isRequired,
  connector: PropTypes.string,
  groups: PropTypes.shape({
    secrets: PropTypes.array.isRequired,
    config: PropTypes.object.isRequired,
  }).isRequired,
};
