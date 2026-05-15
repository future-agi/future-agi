import { z } from "zod";
import { AGENT_TYPES } from "../agents/constants";

// Minimum number of rows a source dataset must have for the Import Dataset
// scenario path. Kept in lockstep with the BE `no_of_rows.min_value` so the
// Workflow Builder and Import Dataset paths agree on the floor.
export const MIN_DATASET_ROWS = 10;

export const CreateScenarioType = {
  DATASET: "dataset",
  SCRIPT: "script",
  GRAPH: "graph",
  SOP: "sop",
};

export const SourceType = {
  AGENT_DEFINITION: "agent_definition",
  PROMPT: "prompt",
};

export const ScenarioTypeWiseDefaultValues = {
  [CreateScenarioType.DATASET]: {
    datasetId: "",
  },
  [CreateScenarioType.SCRIPT]: {
    scriptUrl: "",
  },
  [CreateScenarioType.GRAPH]: {
    graph: null,
    generateGraph: true,
  },
  [CreateScenarioType.SOP]: {
    sopUrl: "",
  },
};

export const CreateScenarioDefaultValues = {
  agentType: "",
  sourceType: SourceType.AGENT_DEFINITION,
  sourceId: "",
  sourceLabel: "", // Used for auto-generating scenario name, not sent to API
  name: "",
  description: "",
  agentDefinitionId: "",
  agentDefinitionVersionId: "",
  promptTemplateId: "",
  promptVersionId: "",
  customInstructionDisabled: true,
  customInstruction: "",
  kind: CreateScenarioType.GRAPH,
  noOfRows: 20,
  addPersonaAutomatically: true,
  columns: [],
  personas: [],
  config: {
    ...ScenarioTypeWiseDefaultValues[CreateScenarioType.GRAPH],
  },
};

const GraphSchema = z.any();

const CreateScenarioDefaultSchema = {
  agentType: z.enum(["voice", "text"]).optional(),
  sourceType: z
    .enum(["agent_definition", "prompt"])
    .default("agent_definition"),
  sourceId: z.string().min(1, "Source is required"),
  sourceLabel: z.string().optional(), // Used for auto-generating scenario name, not sent to API
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
  agentDefinitionId: z.string().optional(),
  agentDefinitionVersionId: z.string().optional(),
  promptTemplateId: z.string().optional(),
  promptVersionId: z.string().optional(),
  customInstructionDisabled: z.boolean().default(false),
  customInstruction: z.string().optional(),
  noOfRows: z
    .number()
    .min(10, "Minimum 10 Rows are required")
    .max(20000, "Maximum 20000 Rows are allowed"),
  addPersonaAutomatically: z.boolean(),
  columns: z.array(
    z.object({
      name: z
        .string()
        .min(1, "Column name is required")
        .max(50, "Column name must be less than 50 characters"),

      type: z.enum(
        ["text", "boolean", "integer", "float", "json", "array", "datetime"],
        { message: "Data type is required" },
      ),

      description: z
        .string()
        .min(1, "Description is required")
        .max(200, "Description must be less than 200 characters"),
    }),
  ),
  personas: z
    .array(z.object({ id: z.string() }))
    .transform((arr) => arr.map((item) => item.id)),
};

export const CreateScenarioValidationSchema = z
  .discriminatedUnion("kind", [
    z.object({
      ...CreateScenarioDefaultSchema,
      kind: z.literal(CreateScenarioType.DATASET),
      config: z.object({
        datasetId: z.string().min(1, "Dataset is required"),
      }),
    }),
    z.object({
      ...CreateScenarioDefaultSchema,
      kind: z.literal(CreateScenarioType.SCRIPT),
      config: z.object({
        scriptUrl: z.object({
          file: z.instanceof(File),
          name: z.string(),
          size: z.number(),
        }),
      }),
    }),
    z.object({
      ...CreateScenarioDefaultSchema,
      kind: z.literal(CreateScenarioType.SOP),
      config: z.object({
        sopUrl: z.object({
          file: z.instanceof(File),
          name: z.string(),
          size: z.number(),
        }),
      }),
    }),
    z.object({
      ...CreateScenarioDefaultSchema,
      kind: z.literal(CreateScenarioType.GRAPH),
      config: z.object({
        graph: GraphSchema,
        generateGraph: z.boolean(),
      }),
    }),
  ])
  .refine(
    (data) => {
      // Validate agent definition fields when source type is agent_definition
      if (data.sourceType === "agent_definition") {
        return data.agentDefinitionId && data.agentDefinitionId.length > 0;
      }
      return true;
    },
    {
      message: "Agent definition is required",
      path: ["agentDefinitionId"],
    },
  )
  .refine(
    (data) => {
      // Validate agent version when source type is agent_definition
      if (data.sourceType === "agent_definition") {
        return (
          data.agentDefinitionVersionId &&
          data.agentDefinitionVersionId.length > 0
        );
      }
      return true;
    },
    {
      message: "Agent definition version is required",
      path: ["agentDefinitionVersionId"],
    },
  )
  .refine(
    (data) => {
      // Validate prompt fields when source type is prompt
      if (data.sourceType === "prompt") {
        return data.promptTemplateId && data.promptTemplateId.length > 0;
      }
      return true;
    },
    {
      message: "Prompt is required",
      path: ["promptTemplateId"],
    },
  )
  .refine(
    (data) => {
      // Validate prompt version when source type is prompt
      if (data.sourceType === "prompt") {
        return data.promptVersionId && data.promptVersionId.length > 0;
      }
      return true;
    },
    {
      message: "Prompt version is required",
      path: ["promptVersionId"],
    },
  )
  .refine(
    (data) => {
      if (!data.customInstructionDisabled) {
        return (
          data.customInstruction && data.customInstruction.trim().length > 0
        );
      }
      return true;
    },
    {
      message: "Instruction is required",
      path: ["customInstruction"],
    },
  )
  .transform((data) => {
    // Remove customInstruction key if not enabled
    if (data.customInstructionDisabled) {
      const { customInstruction, ...rest } = data;
      return rest;
    }
    return data;
  });

export const getIconForAgentDefinitions = (type) => {
  if (type === AGENT_TYPES.VOICE) {
    return "/assets/icons/ic_call_inbound.svg";
  } else {
    return "/assets/icons/ic_chat_single.svg";
  }
};

/**
 * Generate date string for scenario naming (e.g., "Feb11")
 */
export const getScenarioDateString = () => {
  const now = new Date();
  const month = now.toLocaleString("en-US", { month: "short" });
  const day = now.getDate();
  return `${month}${day}`;
};

/**
 * Extract version number from scenario name matching pattern like "AgentName_Feb11v1"
 * @param {string} name - Scenario name to check
 * @param {string} basePattern - Base pattern without version (e.g., "AgentName_Feb11")
 * @returns {number} - Version number or 0 if no match
 */
export const extractVersionFromScenarioName = (name, basePattern) => {
  // Escape special regex characters in basePattern
  const escapedPattern = basePattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`^${escapedPattern}v(\\d+)$`, "i");
  const match = name.match(regex);
  return match ? parseInt(match[1], 10) : 0;
};
