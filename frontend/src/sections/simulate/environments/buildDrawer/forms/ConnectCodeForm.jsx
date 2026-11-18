import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Typography } from "@mui/material";
import GitHubConnectFlow from "src/sections/agent-playground/connect/GitHubConnectFlow";
import UploadConnectFlow from "src/sections/agent-playground/connect/UploadConnectFlow";
import { shownEnvValues } from "src/sections/agent-playground/connect/connectDraft";
import usePanelBuild from "../../hooks/usePanelBuild";
import { DEFAULT_SCENARIOS } from "../../panels/scenarioCountRules";
import { T12 } from "../drawerStyles";
import CodeEnvValues from "./CodeEnvValues";
import { Field, FormCard, MONO, ToggleRow } from "./formBits";
import TextLink from "./shared/TextLink";

const draftFor = (kind, picked, form) => {
  const shared = {
    displayName: form.name.trim(),
    envValues: shownEnvValues(
      { values: form.values, extra: form.extra },
      picked.envNames || [],
    ),
    egress: form.egress.trim() || null,
    secretFiles: form.secretFiles,
    scenarioCount: Number(form.scenarioCount) || undefined,
    observabilityEnabled: form.observabilityEnabled,
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

const formFor = (picked) => ({
  name: picked.name || "",
  values: {},
  extra: [],
  egress: "",
  secretFiles: [],
  scenarioCount: DEFAULT_SCENARIOS,
  observabilityEnabled: false,
});

export default function ConnectCodeForm({ kind, onBusyChange }) {
  const [picked, setPicked] = useState(null);
  const [form, setForm] = useState(null);
  const build = usePanelBuild();

  if (!picked) {
    const Flow = kind === "upload" ? UploadConnectFlow : GitHubConnectFlow;
    return (
      <Flow
        onComplete={(next) => {
          build.resetPreflight();
          setPicked(next);
          setForm(formFor(next));
        }}
      />
    );
  }

  const set = (field) => (value) => {
    setForm((f) => ({
      ...f,
      [field]: typeof value === "function" ? value(f[field]) : value,
    }));
    build.resetPreflight();
  };

  return (
    <FormCard
      intro="We build the environment from this agent in our sandbox. Its agent is made with it, so there's nothing to wait for."
      build={build}
      scenarioCount={form.scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!!form.name.trim()}
      blockedReason="Name the agent, then preflight checks it and its values."
      onRun={() => build.runPreflight(draftFor(kind, picked, form))}
      onBusyChange={onBusyChange}
    >
      <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
        <Box component="span" sx={{ fontFamily: MONO, color: "text.primary" }}>
          {whereOf(kind, picked)}
        </Box>
        {" · "}
        <TextLink
          onClick={() => {
            setPicked(null);
            setForm(null);
          }}
        >
          Change
        </TextLink>
      </Typography>
      <Field
        label="Name"
        required
        value={form.name}
        onChange={set("name")}
        placeholder="Support agent"
      />
      <CodeEnvValues
        names={picked.envNames || []}
        values={form.values}
        onValues={set("values")}
        extra={form.extra}
        onExtra={set("extra")}
        egress={form.egress}
        onEgress={set("egress")}
        secretFiles={form.secretFiles}
        onSecretFiles={set("secretFiles")}
      />
      <ToggleRow
        title="Enable observability"
        body={
          form.observabilityEnabled
            ? "Its traces come into Observe once the code sends them."
            : "Off. Its traces stay out of Observe."
        }
        hint="We can observe the agent only if its code already sends telemetry (OpenTelemetry or traceAI). Without it the environment still builds."
        checked={form.observabilityEnabled}
        onChange={set("observabilityEnabled")}
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
