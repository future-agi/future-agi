const SCHEMA_VERSION = "futureagi.harness-job.v1";

// A version the Build environment dialog offers: checked, or from before
// versioning.
export const readyToBuild = (v) =>
  !["pending", "failed", "discarded"].includes(v.check);

const sourceOf = (source) => {
  if (source?.kind === "archive") {
    return {
      kind: "archive",
      archive_artifact_id: source.archive_artifact_id,
    };
  }
  if (source?.kind === "github") {
    return {
      kind: "github",
      repository: source.repository,
      ...(source.ref ? { ref: source.ref } : {}),
      visibility: "public",
    };
  }
  return { kind: "provider", visibility: "public" };
};

// The creation request for an environment bound to the agent: the platform
// takes the source and credential names from the version, so only the name,
// the scope and which version to build from travel.
export const buildFromAgentPayload = ({
  agent,
  version,
  name,
  scenarioCount,
  parallelism = 1,
}) => ({
  schema_version: SCHEMA_VERSION,
  scenario_count: scenarioCount,
  ...(parallelism > 1 ? { runtime: { parallelism } } : {}),
  artifacts: { level: "full" },
  source: sourceOf(version.source),
  agent: { connector: "auto", config: {}, secret_refs: {} },
  metadata: {
    name,
    authoring_key: name,
    agent_definition_id: agent.id,
    agent_version_id: version.id,
  },
});
