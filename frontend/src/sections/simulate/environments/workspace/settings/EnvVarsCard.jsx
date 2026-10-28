import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import SettingsCard from "./SettingsCard";
import {
  SecretRefEditor,
  SecretRefText,
  SetRefAction,
} from "./SecretRefControls";
import {
  RAW_KEY_MESSAGE,
  looksLikeRawKey,
  validateSecretRef,
} from "./secretRefs";
import {
  DIVIDER_SX,
  KEY_W,
  KIND_CHIP_SX,
  MONO,
  SECRET_MASK,
} from "./settings.constants";

const saved = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });

function AddVariableRow({ taken, disabled }) {
  const [draft, setDraft] = useState({ key: "", value: "", secret: true });
  const [error, setError] = useState(null);
  const edit = (patch) => {
    setDraft((d) => ({ ...d, ...patch }));
    setError(null);
  };
  const add = () => {
    const key = draft.key.trim();
    if (!key) return;
    if (taken.includes(key)) {
      setError(`${key} is already set.`);
      return;
    }
    if (draft.secret) {
      const res = validateSecretRef(draft.value);
      if (!res.ok) {
        setError(res.error);
        return;
      }
    } else if (looksLikeRawKey(draft.value)) {
      setError(RAW_KEY_MESSAGE);
      return;
    }
    saved();
    setDraft({ key: "", value: "", secret: true });
  };
  return (
    <Box
      sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <TextField
          size="small"
          placeholder="ENV_KEY_…"
          value={draft.key}
          disabled={disabled}
          onChange={(e) => edit({ key: e.target.value })}
          inputProps={{ "aria-label": "New variable name", spellCheck: false }}
          sx={{
            width: KEY_W,
            flexShrink: 0,
            "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
          }}
        />
        <TextField
          size="small"
          placeholder={draft.secret ? "vault://acme/sim/…" : "Value"}
          value={draft.value}
          disabled={disabled}
          error={!!error}
          onChange={(e) => edit({ value: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          inputProps={{
            "aria-label": "New variable value",
            spellCheck: false,
            autoComplete: "off",
          }}
          sx={{
            flex: 1,
            maxWidth: 360,
            "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
          }}
        />
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Switch
            size="small"
            checked={draft.secret}
            disabled={disabled}
            onChange={(e) => edit({ secret: e.target.checked })}
            inputProps={{ role: "switch", "aria-label": "Secret" }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Secret
          </Typography>
        </Stack>
        <Button
          variant="outlined"
          size="small"
          onClick={add}
          disabled={disabled || !draft.key.trim()}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          Add
        </Button>
      </Stack>
      <Typography
        role={error ? "alert" : undefined}
        sx={{
          typography: "s3",
          color: error ? "error.main" : "text.subtitle",
          mt: 0.75,
          pl: { md: `calc(${KEY_W}px + 8px)` },
        }}
      >
        {error ||
          (draft.secret
            ? "A secret is added as a reference. Paste its path in your secret manager, not the key."
            : "Plain configuration. Stored and shown as typed.")}
      </Typography>
    </Box>
  );
}
AddVariableRow.propTypes = {
  taken: PropTypes.arrayOf(PropTypes.string).isRequired,
  disabled: PropTypes.bool,
};

function VariableRow({ row, disabled }) {
  const [editing, setEditing] = useState(false);
  return (
    <Box sx={{ px: 2.5, py: 1.125 }}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            fontFamily: MONO,
            width: KEY_W,
            flexShrink: 0,
            pr: 1,
            // A long identifier (GOOGLE_APPLICATION_CREDENTIALS_JSON) has no
            // space to wrap on, so it breaks within its column.
            overflowWrap: "anywhere",
          }}
        >
          {row.key}
        </Typography>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {row.ref ? (
            <SecretRefText value={row.ref} />
          ) : (
            <Typography
              noWrap
              sx={{
                typography: "s2",
                fontFamily: MONO,
                color: "text.subtitle",
              }}
            >
              {row.masked ? SECRET_MASK : row.value}
            </Typography>
          )}
        </Box>
        {row.masked && (
          <CustomTooltip
            show
            arrow
            size="small"
            title="Stored encrypted. The value is never shown."
          >
            <Iconify
              icon="solar:lock-keyhole-minimalistic-linear"
              width={14}
              sx={{ color: "text.subtitle", flexShrink: 0 }}
            />
          </CustomTooltip>
        )}
        <Chip size="small" label={row.kind} sx={KIND_CHIP_SX} />
        {row.masked && !editing && (
          <SetRefAction disabled={disabled} onClick={() => setEditing(true)} />
        )}
        <IconButton
          size="small"
          disabled={disabled}
          onClick={saved}
          aria-label={`Remove ${row.key}`}
        >
          <Iconify
            icon="solar:trash-bin-trash-linear"
            width={16}
            sx={{ color: "text.subtitle" }}
          />
        </IconButton>
      </Stack>
      {editing && (
        <Box sx={{ pl: { md: `calc(${KEY_W}px + 12px)` }, pt: 1 }}>
          <SecretRefEditor
            placeholder={row.ref || "vault://acme/sim/api_key"}
            onSave={() => {
              saved();
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        </Box>
      )}
    </Box>
  );
}
VariableRow.propTypes = {
  row: PropTypes.shape({
    key: PropTypes.string.isRequired,
    value: PropTypes.string,
    ref: PropTypes.string,
    kind: PropTypes.string.isRequired,
    masked: PropTypes.bool,
  }).isRequired,
  disabled: PropTypes.bool,
};

// The real variables the build recorded (secrets masked, config in clear).
// Adding a variable, setting a secret's reference and removing one have no
// API yet: they are flagged and save nothing.
export default function EnvVarsCard({
  rows,
  loading = false,
  disabled = false,
}) {
  let body;
  if (loading) {
    body = (
      <Typography sx={{ p: 2.5, typography: "s2", color: "text.secondary" }}>
        Loading…
      </Typography>
    );
  } else if (!rows.length) {
    body = (
      <Typography sx={{ p: 2.5, typography: "s2", color: "text.secondary" }}>
        No variables were recorded for this environment.
      </Typography>
    );
  } else {
    body = (
      <Stack divider={<Box sx={DIVIDER_SX} />}>
        {rows.map((row) => (
          <VariableRow
            key={`${row.kind}:${row.key}`}
            row={row}
            disabled={disabled}
          />
        ))}
      </Stack>
    );
  }
  return (
    <SettingsCard
      label="Environment variables"
      title="Environment variables"
      subtitle="Set when the environment was built. Secret values are never shown."
      action={<UiOnlyFlag feature="environmentSettings" />}
    >
      {body}
      <AddVariableRow taken={rows.map((r) => r.key)} disabled={disabled} />
    </SettingsCard>
  );
}
EnvVarsCard.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  loading: PropTypes.bool,
  disabled: PropTypes.bool,
};

// Credential files are mounted into the environment by name. Shown only when
// the environment has any.
export function CredentialFilesCard({ files }) {
  if (!files.length) return null;
  return (
    <SettingsCard
      label="Credential files"
      title="Credential files"
      subtitle="Mounted into the environment by name. Contents and the original filename are not stored."
    >
      <Stack divider={<Box sx={DIVIDER_SX} />}>
        {files.map((f) => (
          <Stack
            key={f.environment_name}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1.375 }}
          >
            <Typography
              sx={{
                flex: 1,
                minWidth: 0,
                typography: "s2",
                fontWeight: 600,
                fontFamily: MONO,
                overflowWrap: "anywhere",
              }}
            >
              {f.environment_name}
            </Typography>
            <Chip size="small" label="File" sx={KIND_CHIP_SX} />
          </Stack>
        ))}
      </Stack>
    </SettingsCard>
  );
}
CredentialFilesCard.propTypes = {
  files: PropTypes.arrayOf(PropTypes.object).isRequired,
};
