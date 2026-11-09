import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { switchoverSentence } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import DeployIcon from "../components/DeployIcon";
import SecretsField from "./SecretsField";

const SECRETS_HINT =
  "Picked by name from this workspace's vault and injected when the revision starts. Values are never shown here or stored with the deployment.";

function Field({ label, hint, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
      <Box>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {label}
        </Typography>
        {hint && (
          <Typography typography="s2" color="text.secondary">
            {hint}
          </Typography>
        )}
      </Box>
      {children}
    </Box>
  );
}

Field.propTypes = {
  label: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};

function EnvRows({ rows, problems, onChange }) {
  const [visibleIndex, setVisibleIndex] = useState(null);
  const set = (index, patch) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <Box
      data-testid="env-rows"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      {rows.map((row, index) => (
        <Box
          key={index}
          sx={{
            display: "flex",
            gap: 1,
            alignItems: "flex-start",
            flexWrap: { xs: "wrap", sm: "nowrap" },
          }}
        >
          <TextField
            size="small"
            placeholder="NAME"
            value={row.key}
            onChange={(e) => set(index, { key: e.target.value })}
            error={Boolean(problems[index])}
            helperText={problems[index] || " "}
            inputProps={{
              "aria-label": `Variable ${index + 1} name`,
              spellCheck: false,
            }}
            sx={{
              flex: { xs: "1 1 100%", sm: "0 0 38%" },
              "& input": { fontFamily: "monospace" },
            }}
          />
          <TextField
            size="small"
            type={visibleIndex === index ? "text" : "password"}
            placeholder="value"
            value={row.value}
            onChange={(e) => set(index, { value: e.target.value })}
            helperText=" "
            inputProps={{
              "aria-label": `Variable ${index + 1} value`,
              spellCheck: false,
              autoComplete: "new-password",
            }}
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <Tooltip
                    title={visibleIndex === index ? "Hide value" : "Show value"}
                  >
                    <IconButton
                      edge="end"
                      aria-label={
                        visibleIndex === index
                          ? `Hide variable ${index + 1} value`
                          : `Show variable ${index + 1} value`
                      }
                      onClick={() =>
                        setVisibleIndex((current) =>
                          current === index ? null : index,
                        )
                      }
                    >
                      <Typography component="span" sx={{ fontSize: 12 }}>
                        {visibleIndex === index ? "Hide" : "Show"}
                      </Typography>
                    </IconButton>
                  </Tooltip>
                </InputAdornment>
              ),
            }}
            sx={{ flex: 1, "& input": { fontFamily: "monospace" } }}
          />
          <Tooltip title="Remove">
            <IconButton
              aria-label={`Remove variable ${index + 1}`}
              onClick={() => onChange(rows.filter((_, i) => i !== index))}
              sx={{ mt: 0.25 }}
            >
              <DeployIcon name="close" size={16} />
            </IconButton>
          </Tooltip>
        </Box>
      ))}
      <Box>
        <Button
          size="small"
          color="inherit"
          onClick={() => onChange([...rows, { key: "", value: "" }])}
          startIcon={<DeployIcon name="plus" size={16} />}
          data-testid="add-env"
        >
          Add variable
        </Button>
      </Box>
    </Box>
  );
}

EnvRows.propTypes = {
  rows: PropTypes.array.isRequired,
  problems: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

/** Step 2: name, plain variables, secrets by name, and the Studio page. */
export default function SettingsStep({
  value,
  checks,
  nameLocked,
  existing,
  onChange,
}) {
  const set = (patch) => onChange({ ...value, ...patch });
  const noDowntime = useOmegaPart(OMEGA_PART.DEPLOY_NO_DOWNTIME);
  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 3, maxWidth: 720 }}
    >
      <Field
        label="Name"
        hint={
          existing
            ? `Deploys revision ${existing.revisionCount + 1} of ${existing.name}. ${switchoverSentence(noDowntime, existing.revisionCount + 1)}`
            : "Lowercase letters, digits and dashes. Deploying again under the same name adds a revision."
        }
      >
        <TextField
          size="small"
          value={value.name}
          disabled={nameLocked}
          onChange={(e) => set({ name: e.target.value.trim() })}
          error={Boolean(checks.nameError)}
          helperText={checks.nameError}
          inputProps={{
            "aria-label": "Deployment name",
            spellCheck: false,
            "data-testid": "deployment-name",
          }}
          sx={{ maxWidth: 360, "& input": { fontFamily: "monospace" } }}
        />
      </Field>
      <Field
        label="Environment variables"
        hint="Plain settings the project reads at start. They are stored with the revision, and its page shows their names only; put secrets in the vault instead."
      >
        <EnvRows
          rows={value.rows}
          problems={checks.rowProblems}
          onChange={(rows) => set({ rows })}
        />
      </Field>
      <SecretsField
        selected={value.secretRefs}
        onChange={(secretRefs) => set({ secretRefs })}
        renderField={(body) => (
          <Field label="Secrets" hint={SECRETS_HINT}>
            {body}
          </Field>
        )}
      />
      <FormControlLabel
        control={
          <Switch
            checked={value.studio}
            onChange={(e) => set({ studio: e.target.checked })}
            inputProps={{ "aria-label": "Serve the Studio page" }}
          />
        }
        label={
          <Box>
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Serve the Studio page
            </Typography>
            <Typography typography="s2" color="text.secondary">
              A page at the deployment&apos;s address where you can run its
              entries. It asks for the access token.
            </Typography>
          </Box>
        }
        sx={{
          alignItems: "flex-start",
          m: 0,
          gap: 1,
          "& .MuiSwitch-root": { mt: -0.5 },
        }}
      />
    </Box>
  );
}

SettingsStep.propTypes = {
  value: PropTypes.shape({
    name: PropTypes.string.isRequired,
    rows: PropTypes.array.isRequired,
    secretRefs: PropTypes.arrayOf(PropTypes.string).isRequired,
    studio: PropTypes.bool.isRequired,
  }).isRequired,
  checks: PropTypes.object.isRequired,
  nameLocked: PropTypes.bool,
  existing: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
