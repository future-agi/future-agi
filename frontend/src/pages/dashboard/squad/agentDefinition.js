import { strFromU8, unzipSync } from "fflate/browser";

export const AGENT_DEFINITION_SCHEMA = "omega-squad-agent-definition/v1";
export const MAX_DEFINITION_BYTES = 262144;
const MAX_ARCHIVE_ENTRIES = 4;

function record(value, required, optional = []) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    required.some((key) => !Object.hasOwn(value, key)) ||
    Object.keys(value).some(
      (key) => !required.includes(key) && !optional.includes(key),
    )
  ) {
    throw new Error("The definition contains missing or unsupported fields.");
  }
}
function text(value, max, label) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    new TextEncoder().encode(value).length > max
  ) {
    throw new Error(
      `${label} must be nonempty text within the supported size limit.`,
    );
  }
  return value;
}
function definition(value) {
  record(
    value,
    ["name", "role", "purpose", "responsibilities", "workMode"],
    ["examples", "modelId"],
  );
  if (
    !Array.isArray(value.responsibilities) ||
    value.responsibilities.length > 64
  )
    throw new Error("Responsibilities must contain at most 64 entries.");
  if (!["on_demand", "always_on"].includes(value.workMode))
    throw new Error("The work mode is unsupported.");
  const examples = value.examples ?? [];
  if (!Array.isArray(examples) || examples.length > 16)
    throw new Error("Examples must contain at most 16 prompt/response pairs.");
  return {
    name: text(value.name, 200, "Name"),
    role: text(value.role, 200, "Role"),
    purpose: text(value.purpose, 4000, "Goal"),
    responsibilities: value.responsibilities.map((entry) =>
      text(entry, 1000, "Responsibility"),
    ),
    workMode: value.workMode,
    examples: examples.map((entry) => {
      record(entry, ["prompt", "response"]);
      return {
        prompt: text(entry.prompt, 2000, "Example prompt"),
        response: text(entry.response, 2000, "Example response"),
      };
    }),
    ...(value.modelId !== undefined
      ? { modelId: text(value.modelId, 256, "Model") }
      : {}),
  };
}

/** Portable authored definition only. Runtime identities, connections and credentials are never copied. */
export function exportAgentDefinition(agent) {
  if (!Array.isArray(agent.examples))
    throw new Error(
      "The server did not return complete authoring fields. Refresh the agent or update the runtime before exporting.",
    );
  const value = {
    schemaVersion: AGENT_DEFINITION_SCHEMA,
    definition: definition({
      name: agent.name,
      role: agent.role,
      purpose: agent.purpose,
      responsibilities: agent.responsibilities,
      workMode: agent.workMode,
      examples: agent.examples,
      ...(typeof agent.modelId === "string" ? { modelId: agent.modelId } : {}),
    }),
    setupRequired: {
      environment: Boolean(agent.environment),
      connections: Boolean(agent.connectorBindings?.length),
    },
  };
  const serialized = JSON.stringify(value, null, 2) + "\n";
  if (new TextEncoder().encode(serialized).length > MAX_DEFINITION_BYTES)
    throw new Error("The exported definition exceeds the supported file size.");
  return serialized;
}

export function importAgentDefinition(serialized) {
  if (
    typeof serialized !== "string" ||
    new TextEncoder().encode(serialized).length > MAX_DEFINITION_BYTES
  )
    throw new Error("The definition file must be no larger than 256 KiB.");
  let value;
  try {
    value = JSON.parse(serialized);
  } catch {
    throw new Error("Choose a valid Omega Squad definition JSON file.");
  }
  record(value, ["schemaVersion", "definition", "setupRequired"]);
  if (value.schemaVersion !== AGENT_DEFINITION_SCHEMA)
    throw new Error("This definition version is unsupported.");
  record(value.setupRequired, ["environment", "connections"]);
  if (
    typeof value.setupRequired.environment !== "boolean" ||
    typeof value.setupRequired.connections !== "boolean"
  )
    throw new Error("The setup requirements are malformed.");
  return {
    definition: definition(value.definition),
    setupRequired: { ...value.setupRequired },
  };
}

/**
 * Import the ZIP form used by the customer console. The archive is deliberately
 * strict: one manifest, no directories, no extra files and a bounded expanded
 * size. Runtime identities, connector bindings and credentials are still
 * rejected by the JSON schema above.
 */
export function importAgentDefinitionZip(input) {
  if (!(input instanceof Uint8Array) || input.byteLength > MAX_DEFINITION_BYTES)
    throw new Error("The definition archive must be no larger than 256 KiB.");
  let files;
  try {
    files = unzipSync(new Uint8Array(input));
  } catch (error) {
    throw new Error(
      `Choose a valid Omega Squad definition ZIP file: ${error?.message || "invalid archive"}`,
    );
  }
  const names =
    files &&
    !Array.isArray(files) &&
    typeof files === "object" &&
    !(files instanceof Uint8Array)
      ? Object.keys(files)
      : [];
  if (
    names.length < 1 ||
    names.length > MAX_ARCHIVE_ENTRIES ||
    names.some(
      (name) =>
        !name ||
        name.endsWith("/") ||
        name.includes("..") ||
        name.startsWith("/"),
    )
  ) {
    throw new Error(
      `The definition archive contains unsupported files${names.length ? `: ${names.join(", ")}` : ""}.`,
    );
  }
  const manifestNames = names.filter(
    (name) => name === "omega-agent.json" || name === "agent.json",
  );
  if (manifestNames.length !== 1 || names.length !== 1)
    throw new Error(
      "The definition archive must contain exactly one omega-agent.json manifest.",
    );
  const manifest = files[manifestNames[0]];
  if (
    !(manifest instanceof Uint8Array) ||
    manifest.byteLength > MAX_DEFINITION_BYTES
  )
    throw new Error("The definition manifest exceeds the supported size.");
  return importAgentDefinition(strFromU8(new Uint8Array(manifest)));
}

export function importedDefinitionDraft(portable, duplicate = false) {
  const source = portable.definition;
  return {
    name: duplicate ? copyName(source.name) : source.name,
    role: source.role,
    purpose: source.purpose,
    responsibilities: source.responsibilities.join("\n"),
    sourceResponsibilities: [...source.responsibilities],
    workMode: source.workMode,
    examples: source.examples,
    modelId: source.modelId || "",
    modelChanged: Boolean(source.modelId),
    environmentId: "",
    environmentRevision: "",
    environmentChanged: false,
    importedDefinition: true,
  };
}

function copyName(name) {
  let stem = name;
  while (new TextEncoder().encode(`${stem} copy`).length > 200)
    stem = [...stem].slice(0, -1).join("");
  return `${stem} copy`;
}
