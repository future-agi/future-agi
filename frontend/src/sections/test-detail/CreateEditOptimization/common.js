import { z } from "zod";

export const OPTIMIZER_OPTIONS = [
  {
    label: "Random Search",
    value: "random_search",
    description: "Simple random variations",
    icon: "/assets/icons/theorems/ic_randomsearch.svg",
  },
  {
    label: "Bayesian Search",
    value: "bayesian",
    icon: "/assets/icons/theorems/ic_bayesian_theorem.svg",
    description: "Intelligent hyperparameter tuning with Optuna",
  },
  {
    label: "ProTeGi",
    value: "protegi",
    icon: "/assets/icons/theorems/ic_protegi.svg",
    description: "Textual gradients for iterative improvement",
  },
  {
    label: "Meta-Prompt",
    value: "metaprompt",
    icon: "/assets/icons/theorems/ic_metaprompt.svg",
    description: "Uses powerful models to analyze and rewrite",
  },
  {
    label: "PromptWizard",
    value: "promptwizard",
    icon: "/assets/icons/theorems/ic_promptwizard.svg",
    description: "Mutation, critique, and refinement pipeline",
  },
  {
    label: "GEPA",
    value: "gepa",
    icon: "/assets/icons/theorems/ic_gepa.svg",
    description: "Genetic Pareto evolutionary optimization",
  },
  {
    label: "Agent Learning Kit",
    value: "agent_learning_kit",
    icon: "/assets/icons/theorems/ic_metaprompt.svg",
    description: "Agent-learning-kit candidate optimization (TH-5642)",
  },
];

export const OPTIMIZER_TYPE = {
  RANDOM_SEARCH: "random_search",
  BAYESIAN: "bayesian",
  PROTEGI: "protegi",
  METAPROMPT: "metaprompt",
  PROMPTWIZARD: "promptwizard",
  GEPA: "gepa",
  AGENT_LEARNING_KIT: "agent_learning_kit",
};

const RandomSearchOptimizerSchema = z.object({
  taskDescription: z.string().optional(),
  numVariations: z
    .number({ invalid_type_error: "Number of variations is required" })
    .min(1, "Number of variations must be at least 1"),
});

const ProTeGiOptimzerSchema = z.object({
  taskDescription: z.string().optional(),
  numGradients: z
    .number({ invalid_type_error: "Number of gradients is required" })
    .min(1, "Number of gradients must be at least 1"),
  errorsPerGradient: z
    .number({ invalid_type_error: "Errors per gradient is required" })
    .min(1, "Errors per gradient must be at least 1"),
  promptsPerGradient: z
    .number({ invalid_type_error: "Prompts per gradient is required" })
    .min(1, "Prompts per gradient must be at least 1"),
  beamSize: z
    .number({ invalid_type_error: "Beam size is required" })
    .min(1, "Beam size must be at least 1"),
  numRounds: z
    .number({ invalid_type_error: "Number of rounds is required" })
    .min(1, "Number of rounds must be at least 1"),
});

const MetaPromptOptimizerSchema = z.object({
  taskDescription: z.string().optional(),
  numRounds: z
    .number({ invalid_type_error: "Number of rounds is required" })
    .min(1, "Number of rounds must be at least 1"),
});
const BayesianSearchOptimizerSchema = z
  .object({
    taskDescription: z.string().optional(),
    minExamples: z
      .number({ invalid_type_error: "Minimum number of examples is required" })
      .min(1, "Minimum examples must be at least 1"),
    maxExamples: z
      .number({ invalid_type_error: "Maximum number of examples is required" })
      .min(1, "Maximum examples must be at least 1"),
    nTrials: z
      .number({ invalid_type_error: "Number of trials is required" })
      .min(1, "Number of trials must be at least 1"),
  })
  .refine((data) => data.minExamples < data.maxExamples, {
    message: "Min examples must be less than max examples",
    path: ["minExamples"],
  })
  .refine((data) => data.maxExamples > data.minExamples, {
    message: "Max examples must be greater than min examples",
    path: ["maxExamples"],
  });

const PromptWizardOptimizerSchema = z.object({
  taskDescription: z.string().optional(),
  mutateRounds: z
    .number({ invalid_type_error: "Mutated rounds is required" })
    .min(1, "Mutated rounds must be at least 1"),
  refineIterations: z
    .number({ invalid_type_error: "Refined iterations is required" })
    .min(1, "Refined iterations must be at least 1"),
  beamSize: z
    .number({ invalid_type_error: "Beam size is required" })
    .min(1, "Beam size must be at least 1"),
});

const GEPAOptimizerSchema = z.object({
  taskDescription: z.string().optional(),
  maxMetricCalls: z
    .number({ invalid_type_error: "Max metric calls is required" })
    .min(1, "Max metric calls must be at least 1"),
});

// Whole-agent optimization: the backend derives base_agent from the run's
// agent definition; searchSpace (dot-path -> candidate values, JSON) widens
// the search beyond instructions (e.g. {"model": [...], "voice_id": [...]}).
const AgentLearningKitOptimizerSchema = z.object({
  taskDescription: z.string().optional(),
  searchSpace: z
    .string()
    .optional()
    .refine(
      (value) => {
        if (!value || !value.trim()) return true;
        try {
          const parsed = JSON.parse(value);
          return (
            parsed &&
            typeof parsed === "object" &&
            !Array.isArray(parsed) &&
            Object.values(parsed).every((v) => Array.isArray(v))
          );
        } catch {
          return false;
        }
      },
      {
        message:
          'Must be a JSON object mapping config paths to value lists, e.g. {"model": ["gpt-4o", "gpt-4o-mini"]}',
      },
    ),
  dryRun: z.boolean().optional(),
});

export const createEditOptimizerSchema = z.discriminatedUnion("optimiserType", [
  z.object({
    optimiserType: z.literal("random_search"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: RandomSearchOptimizerSchema,
  }),
  z.object({
    optimiserType: z.literal("protegi"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: ProTeGiOptimzerSchema,
  }),
  z.object({
    optimiserType: z.literal("bayesian"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: BayesianSearchOptimizerSchema,
  }),
  z.object({
    optimiserType: z.literal("metaprompt"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: MetaPromptOptimizerSchema,
  }),
  z.object({
    optimiserType: z.literal("promptwizard"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: PromptWizardOptimizerSchema,
  }),
  z.object({
    optimiserType: z.literal("gepa"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: GEPAOptimizerSchema,
  }),
  z.object({
    optimiserType: z.literal("agent_learning_kit"),
    name: z.string().min(1, "Name is required"),
    model: z.string().min(1, "Model is required"),
    configuration: AgentLearningKitOptimizerSchema,
  }),
]);

export const KeyOptimizerMapping = {
  random_search: "Random Search",
  protegi: "ProTeGi",
  bayesian: "Bayesian Search",
  metaprompt: "Meta-Prompt",
  promptwizard: "PromptWizard",
  gepa: "GEPA",
  agent_learning_kit: "Agent Learning Kit",
};

export const OptimizerConfigurationMapping = {
  random_search: {
    taskDescription: "",
    numVariations: 3,
  },
  protegi: {
    taskDescription: "",
    beamSize: 4,
    numGradients: 4,
    errorsPerGradient: 4,
    promptsPerGradient: 1,
    numRounds: 3,
  },
  bayesian: {
    taskDescription: "",
    minExamples: 2,
    maxExamples: 4,
    nTrials: 5,
  },
  metaprompt: {
    taskDescription: "",
    numRounds: 4,
  },
  promptwizard: {
    taskDescription: "",
    mutateRounds: 3,
    refineIterations: 2,
    beamSize: 2,
  },
  gepa: {
    taskDescription: "",
    maxMetricCalls: 40,
  },
  agent_learning_kit: {
    taskDescription: "",
    searchSpace: "",
    dryRun: false,
  },
};

export const ConfigurationSnakeToCamel = {
  num_variations: "numVariations",
  min_examples: "minExamples",
  max_examples: "maxExamples",
  n_trials: "nTrials",
  num_gradients: "numGradients",
  errors_per_gradient: "errorsPerGradient",
  prompts_per_gradient: "promptsPerGradient",
  task_description: "taskDescription",
  mutate_rounds: "mutateRounds",
  refine_iterations: "refineIterations",
  beam_size: "beamSize",
  num_rounds: "numRounds",
  max_metric_calls: "maxMetricCalls",
};

export const mapConfigurationToForm = (configuration = {}) =>
  Object.entries(configuration || {}).reduce((acc, [key, value]) => {
    const camelKey = ConfigurationSnakeToCamel[key] ?? key;
    acc[camelKey] = value;
    return acc;
  }, {});
