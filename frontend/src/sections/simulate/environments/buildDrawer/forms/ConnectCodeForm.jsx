import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Typography } from "@mui/material";
import GitHubConnectFlow from "src/sections/agent-playground/connect/GitHubConnectFlow";
import UploadConnectFlow from "src/sections/agent-playground/connect/UploadConnectFlow";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { T12 } from "../drawerStyles";
import { FormCard, MONO, ToggleRow } from "./formBits";
import TextLink from "./shared/TextLink";

const draftFor = (kind, picked, scenarioCount, observabilityEnabled) => {
  const shared = {
    displayName: picked.name,
    envValues: picked.envValues,
    scenarioCount: Number(scenarioCount) || undefined,
    observabilityEnabled,
  };
  if (kind === "upload") {
    return {
      ...shared,
      kind: "upload",
      folderName: picked.folderName,
      entry: picked.entry,
      archive_artifact_id: picked.source?.archive_artifact_id,
    };
  }
  const { source } = picked;
  return {
    ...shared,
    kind: "repo",
    provider: "github",
    value: source.repository,
    ref: source.ref || "",
    subfolder: source.subfolder || "",
    commitSha: source.commitSha || "",
    entry: picked.entry,
    visibility: source.visibility || "public",
    installationId: source.installation_id || null,
  };
};

const whereOf = (kind, picked) => {
  if (kind === "upload") return picked.folderName || "Uploaded folder";
  const { source } = picked;
  const folder = source.subfolder ? ` · ${source.subfolder}/` : "";
  return `${source.repository}${source.ref ? ` at ${source.ref}` : ""}${folder}`;
};

export default function ConnectCodeForm({ kind, onBusyChange }) {
  const [picked, setPicked] = useState(null);
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [observabilityEnabled, setObservabilityEnabled] = useState(false);
  const build = usePanelBuild();

  if (!picked) {
    const Flow = kind === "upload" ? UploadConnectFlow : GitHubConnectFlow;
    return (
      <Flow
        completeLabel="Next"
        onComplete={(next) => {
          build.resetPreflight();
          setPicked(next);
        }}
      />
    );
  }

  const change = (setter) => (value) => {
    setter(value);
    build.resetPreflight();
  };

  return (
    <FormCard
      intro="We build the environment from this agent in our sandbox. Its agent is made with it, so there's nothing to wait for."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={change(setScenarioCount)}
      canRun
      onRun={() =>
        build.runPreflight(
          draftFor(kind, picked, scenarioCount, observabilityEnabled),
        )
      }
      onBusyChange={onBusyChange}
    >
      <Box>
        <Typography sx={{ ...T12, fontWeight: 600 }}>{picked.name}</Typography>
        <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
          <Box component="span" sx={{ fontFamily: MONO }}>
            {whereOf(kind, picked)}
          </Box>
          {" · "}
          <TextLink onClick={() => setPicked(null)}>Change</TextLink>
        </Typography>
      </Box>
      <ToggleRow
        title="Enable observability"
        body={
          observabilityEnabled
            ? "Its traces come into Observe once the code sends them."
            : "Off. Its traces stay out of Observe."
        }
        hint="We can observe the agent only if its code already sends telemetry (OpenTelemetry or traceAI). Without it the environment still builds."
        checked={observabilityEnabled}
        onChange={change(setObservabilityEnabled)}
      />
    </FormCard>
  );
}

ConnectCodeForm.propTypes = {
  kind: PropTypes.oneOf(["github", "upload"]).isRequired,
  onBusyChange: PropTypes.func,
};

export function GitHubCodeForm(props) {
  return <ConnectCodeForm kind="github" {...props} />;
}

export function UploadCodeForm(props) {
  return <ConnectCodeForm kind="upload" {...props} />;
}
