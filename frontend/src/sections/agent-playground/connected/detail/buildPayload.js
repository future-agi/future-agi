const SCHEMA_VERSION = "futureagi.harness-job.v1";

// A version the Build environment dialog offers: checked, or from before
// versioning.
export const readyToBuild = (v) =>
  !["pending", "failed", "discarded"].includes(v.check);

// The creation request for an environment bound to the agent: the platform
// takes the source and credential names from the version, so only the name,
// the scope and which version to build from travel.
export const buildFromAgentPayload = ({
  agent,
  version,
  name,
  scenarioCount,
}) => ({
  schema_version: SCHEMA_VERSION,
  scenario_count: scenarioCount,
  artifacts: { level: "standard" },
  source:
    version.source?.kind === "archive"
      ? {
          kind: "archive",
          archive_artifact_id: version.source.archive_artifact_id,
        }
      : {
          kind: "github",
          repository: version.source?.repository,
          ...(version.source?.ref ? { ref: version.source.ref } : {}),
          visibility: "public",
        },
  agent: { connector: "auto", config: {}, secret_refs: {} },
  metadata: {
    name,
    authoring_key: name,
    agent_definition_id: agent.id,
    agent_version_id: version.id,
    agent_policy: "follow",
  },
});
