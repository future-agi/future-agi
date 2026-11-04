import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Radio,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import Field from "src/sections/simulate/environments/components/Field";
import { TextLink } from "./connectBits";
import { frameworkLabel } from "./frameworkLabels";
import { MONO } from "./connectStyles";

const estimateLine = (agent) => {
  const parts = [];
  if (agent.estimates) {
    const {
      tools,
      prompt_files: promptFiles,
      promptFiles: alt,
    } = agent.estimates;
    const prompts = promptFiles ?? alt;
    parts.push(`about ${tools} tool${tools === 1 ? "" : "s"}`);
    if (prompts != null)
      parts.push(`${prompts} prompt file${prompts === 1 ? "" : "s"}`);
  }
  return parts.join(" · ");
};

/** The agents a scan found: pick one, or name a folder the scan missed. */
export function FoundAgents({ scan, picked, onPick, onManual }) {
  if (!scan || scan.status === "pending" || scan.status === "running") {
    return (
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ py: 1 }}>
        <CircularProgress size={16} />
        <Typography sx={{ typography: "s1" }}>
          {scan?.repository
            ? `Reading ${scan.repository}${scan.ref ? ` at ${scan.ref}` : ""} for agents`
            : "Reading the upload for agents"}
        </Typography>
      </Stack>
    );
  }
  if (scan.status === "failed" || scan.status === "error") {
    const reason =
      scan.failure?.message ||
      scan.error?.message ||
      "The scan couldn't finish.";
    return (
      <Stack spacing={1}>
        <Alert severity="warning">{`Couldn't find the agents: ${reason}`}</Alert>
        <TextLink onClick={onManual}>
          Name the agent&apos;s folder yourself
        </TextLink>
      </Stack>
    );
  }
  const agents = scan.agents || [];
  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
        {agents.length
          ? `We found ${agents.length} agent${agents.length === 1 ? "" : "s"}`
          : "We found no agent in this source"}
      </Typography>
      {agents.map((agent) => {
        const selected =
          picked?.subfolder === agent.subfolder && picked?.name === agent.name;
        return (
          <Stack
            key={`${agent.subfolder}:${agent.name}`}
            component="label"
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            data-testid={`found-agent-${agent.name}`}
            sx={{
              px: 1.5,
              py: 1.25,
              borderRadius: "6px",
              border: "1px solid",
              cursor: "pointer",
              borderColor: selected ? "text.primary" : "divider",
              bgcolor: selected ? "action.selected" : "transparent",
            }}
          >
            <Radio
              size="small"
              checked={selected}
              onChange={() => onPick(agent)}
              sx={{ p: 0, mt: 0.25 }}
            />
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="baseline" spacing={1}>
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  {agent.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {frameworkLabel(agent.framework)}
                </Typography>
              </Stack>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  fontFamily: MONO,
                }}
              >
                {agent.subfolder ? `${agent.subfolder}/` : "repository root"}
                {agent.entry ? ` · ${agent.entry}` : ""}
              </Typography>
              {estimateLine(agent) && (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {`${estimateLine(agent)} · ${agent.confidence} confidence`}
                </Typography>
              )}
            </Box>
          </Stack>
        );
      })}
      {scan.truncated && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Only the first 50 are listed.
        </Typography>
      )}
      <TextLink onClick={onManual}>Not in the list? Name its folder</TextLink>
    </Stack>
  );
}
FoundAgents.propTypes = {
  scan: PropTypes.object,
  picked: PropTypes.object,
  onPick: PropTypes.func.isRequired,
  onManual: PropTypes.func.isRequired,
};

/** A folder the scan missed, named by hand. */
export function ManualAgent({ subfolder, onSubfolder, onBack }) {
  return (
    <Stack spacing={1}>
      <Field
        label="Folder of the agent"
        mono
        placeholder="agents/support (empty for the repository root)"
        value={subfolder}
        onChange={onSubfolder}
        helper="Relative to the repository root."
      />
      <TextLink onClick={onBack}>Back to the agents we found</TextLink>
    </Stack>
  );
}
ManualAgent.propTypes = {
  subfolder: PropTypes.string.isRequired,
  onSubfolder: PropTypes.func.isRequired,
  onBack: PropTypes.func.isRequired,
};

/**
 * What the agent is called and the environment values its code reads. Values
 * are kept as the agent's credentials; a name left blank is simply not set.
 */
export function AgentDetails({ name, onName, envNames, values, onValue }) {
  const [extra, setExtra] = useState([]);
  const names = [...envNames, ...extra.filter((n) => !envNames.includes(n))];
  const [draft, setDraft] = useState("");
  const addName = () => {
    const trimmed = draft.trim().toUpperCase();
    if (!trimmed || names.includes(trimmed)) return;
    setExtra((prev) => [...prev, trimmed]);
    setDraft("");
  };
  return (
    <Stack spacing={1.5}>
      <Field
        label="Agent name"
        required
        value={name}
        onChange={onName}
        placeholder="Support agent"
      />
      <Box>
        <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
          Environment values
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1 }}>
          {names.length
            ? "The code reads these names. Values are stored as the agent's credentials and never shown again."
            : "The scan found no environment variable; add any the code needs."}
        </Typography>
        <Stack spacing={1}>
          {names.map((envName) => (
            <TextField
              key={envName}
              size="small"
              fullWidth
              type="password"
              label={envName}
              value={values[envName] ?? ""}
              onChange={(e) => onValue(envName, e.target.value)}
              autoComplete="new-password"
              inputProps={{
                "data-testid": `env-value-${envName}`,
                style: { fontFamily: MONO },
              }}
            />
          ))}
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              size="small"
              placeholder="ANOTHER_NAME"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addName();
                }
              }}
              inputProps={{
                style: { fontFamily: MONO },
                "data-testid": "env-name-draft",
              }}
              sx={{ maxWidth: 260 }}
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
        </Stack>
      </Box>
    </Stack>
  );
}
AgentDetails.propTypes = {
  name: PropTypes.string.isRequired,
  onName: PropTypes.func.isRequired,
  envNames: PropTypes.arrayOf(PropTypes.string).isRequired,
  values: PropTypes.object.isRequired,
  onValue: PropTypes.func.isRequired,
};
