import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { useCredentials } from "../api/queries";
import DeployIcon from "../components/DeployIcon";
import { errorWithoutData } from "../lib/queryState";
import { activeNames } from "../lib/secretForm";
import { referenceableSecrets, secretRefsProblem } from "../lib/settingsForm";
import { injectionNameProblem } from "../lib/vaultNames";
import AddSecretDialog from "../secrets/AddSecretDialog";
import { useCanManageSecrets } from "../secrets/useSecrets";

function SecretRow({ secret, checked, onToggle }) {
  return (
    <FormControlLabel
      disabled={!secret.usable && !checked}
      control={
        <Checkbox
          size="small"
          checked={checked}
          onChange={onToggle}
          inputProps={{ "aria-label": `Secret ${secret.name}` }}
        />
      }
      label={
        <Box
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "baseline",
            flexWrap: "wrap",
          }}
        >
          <Box component="span" sx={{ fontFamily: "monospace", fontSize: 13 }}>
            {secret.name}
          </Box>
          {secret.reason && (
            <Typography component="span" typography="s2" color="text.secondary">
              {secret.reason}
            </Typography>
          )}
        </Box>
      }
    />
  );
}

SecretRow.propTypes = {
  secret: PropTypes.object.isRequired,
  checked: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};

/**
 * The wizard's Secrets field: tick secrets from the workspace vault by name,
 * and (owners and admins) add one without leaving the wizard; a secret added
 * here is ticked at once. A name kept from an earlier revision stays listed
 * even when it is gone from the vault, so it can be unticked (EE refuses the
 * deploy if it no longer resolves); a carried-over name the vault would not
 * inject holds the deploy until it is unticked. The field is left out rather
 * than shown as a dead end when there is nothing to pick or untick and this
 * person cannot add a secret (a member facing an empty vault). A failed vault
 * read stays visible even when nothing is picked.
 */
export default function SecretsField({ selected, onChange, renderField }) {
  const credentials = useCredentials();
  const canManage = useCanManageSecrets();
  const [adding, setAdding] = useState(false);

  const toggle = (name) =>
    onChange(
      selected.includes(name)
        ? selected.filter((n) => n !== name)
        : [...selected, name].sort(),
    );
  const blocked = secretRefsProblem(selected);
  const blockedLine = blocked && (
    <Typography
      typography="s2"
      color="error.main"
      role="alert"
      data-testid="secrets-blocked"
    >
      {blocked}
    </Typography>
  );

  if (credentials.isLoading)
    return renderField(<Skeleton variant="rounded" height={40} />);

  const secrets = referenceableSecrets(credentials.data ?? []);
  const missing = selected.filter(
    (name) => !secrets.some((s) => s.name === name),
  );
  const rows = [
    ...secrets,
    ...missing.map((name) => ({
      name,
      usable: true,
      reason: injectionNameProblem(name) ?? "Not in the vault now.",
    })),
  ];
  const addSecret = canManage && (
    <>
      <Box>
        <Button
          size="small"
          color="inherit"
          onClick={() => setAdding(true)}
          startIcon={<DeployIcon name="plus" size={16} />}
          data-testid="wizard-add-secret"
        >
          Add a secret
        </Button>
      </Box>
      <AddSecretDialog
        open={adding}
        takenNames={activeNames(credentials.data)}
        onClose={() => setAdding(false)}
        onAdded={(record) => {
          if (record?.name && !selected.includes(record.name))
            onChange([...selected, record.name].sort());
        }}
      />
    </>
  );
  // Keep existing read errors visible even when picked names supply rows.
  if (errorWithoutData({ error: credentials.error, data: secrets })) {
    // The names already picked stay listed so they can still be unticked.
    return renderField(
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography
          typography="s2"
          color="text.secondary"
          role="alert"
          data-testid="secrets-unavailable"
        >
          The workspace vault could not be read: {credentials.error.message}
        </Typography>
        {selected.map((name) => (
          <SecretRow
            key={name}
            secret={{ name, usable: true, reason: injectionNameProblem(name) }}
            checked
            onToggle={() => toggle(name)}
          />
        ))}
        {blockedLine}
        {credentials.data != null && addSecret}
      </Box>,
    );
  }

  if (rows.length === 0 && !canManage) return null;

  return renderField(
    <Box
      data-testid="secret-picker"
      sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
    >
      {rows.length === 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="secrets-empty"
        >
          No secrets in this workspace yet. Add the API keys this project reads.
        </Typography>
      )}
      {rows.map((secret) => (
        <SecretRow
          key={secret.name}
          secret={secret}
          checked={selected.includes(secret.name)}
          onToggle={() => toggle(secret.name)}
        />
      ))}
      {blockedLine}
      {addSecret}
    </Box>,
  );
}

SecretsField.propTypes = {
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  /** Wraps the field's body in the wizard's labelled field. */
  renderField: PropTypes.func.isRequired,
};
