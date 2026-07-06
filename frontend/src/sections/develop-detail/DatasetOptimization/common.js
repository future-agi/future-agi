import { z } from "zod";
import { camelToSnakeCase } from "src/utils/utils";

/**
 * Convert object keys from camelCase to snake_case.
 * @deprecated API responses are now snake_case — this is a no-op on new data.
 * TODO: Remove once callers are updated.
 */
export const convertKeysToSnakeCase = (obj) => {
  if (!obj || typeof obj !== "object") return obj;
  return Object.entries(obj).reduce((acc, [key, value]) => {
    acc[camelToSnakeCase(key)] = value;
    return acc;
  }, {});
};

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
];

export const OPTIMIZER_TYPE = {
  RANDOM_SEARCH: "random_search",
  BAYESIAN: "bayesian",
  PROTEGI: "protegi",
  METAPROMPT: "metaprompt",
  PROMPTWIZARD: "promptwizard",
  GEPA: "gepa",
};

export const KeyOptimizerMapping = {
  random_search: "Random Search",
  protegi: "ProTeGi",
  bayesian: "Bayesian Search",
  metaprompt: "Meta-Prompt",
  promptwizard: "PromptWizard",
  gepa: "GEPA",
};

export const normalizeDatasetOptimizationRun = (run = {}) => ({
  ...run,
  optimizationName: run.optimizationName ?? run.optimization_name,
  startedAt: run.startedAt ?? run.started_at,
  optimizerAlgorithm: run.optimizerAlgorithm ?? run.optimizer_algorithm,
  optimizerModelId: run.optimizerModelId ?? run.optimizer_model_id,
  optimizerConfig: run.optimizerConfig ?? run.optimizer_config,
  columnId: run.columnId ?? run.column_id,
});

export const normalizeDatasetOptimizationDetail = (optimization = {}) => ({
  ...optimization,
  optimiserName: optimization.optimiserName ?? optimization.optimiser_name,
  optimiserType: optimization.optimiserType ?? optimization.optimiser_type,
  providerLogo: optimization.providerLogo ?? optimization.provider_logo,
  startTime: optimization.startTime ?? optimization.start_time,
  errorMessage: optimization.errorMessage ?? optimization.error_message,
  columnId: optimization.columnId ?? optimization.column_id,
  columnName: optimization.columnName ?? optimization.column_name,
  bestScore: optimization.bestScore ?? optimization.best_score,
  baselineScore: optimization.baselineScore ?? optimization.baseline_score,
  columnConfig: optimization.columnConfig ?? optimization.column_config,
  optimizerModelId:
    optimization.optimizerModelId ?? optimization.optimizer_model_id,
  userEvalTemplates:
    optimization.userEvalTemplates ?? optimization.user_eval_templates,
});

export const OptimizerConfigurationMapping = {
  random_search: {
    task_description: "",
    num_variations: 3,
  },
  protegi: {
    task_description: "",
    beam_size: 4,
    num_gradients: 4,
    errors_per_gradient: 4,
    prompts_per_gradient: 1,
    num_rounds: 3,
  },
  bayesian: {
    task_description: "",
    min_examples: 2,
    max_examples: 4,
    n_trials: 5,
  },
  metaprompt: {
    task_description: "",
    num_rounds: 4,
  },
  promptwizard: {
    task_description: "",
    mutate_rounds: 3,
    refine_iterations: 2,
    beam_size: 2,
  },
  gepa: {
    task_description: "",
    max_metric_calls: 40,
  },
};

// Zod validation schemas
const RandomSearchOptimizerSchema = z.object({
  task_description: z.string().optional(),
  num_variations: z
    .number({ invalid_type_error: "Number of variations is required" })
    .min(1, "Number of variations must be at least 1"),
});

const ProTeGiOptimzerSchema = z.object({
  task_description: z.string().optional(),
  num_gradients: z
    .number({ invalid_type_error: "Number of gradients is required" })
    .min(1, "Number of gradients must be at least 1"),
  errors_per_gradient: z
    .number({ invalid_type_error: "Errors per gradient is required" })
    .min(1, "Errors per gradient must be at least 1"),
  prompts_per_gradient: z
    .number({ invalid_type_error: "Prompts per gradient is required" })
    .min(1, "Prompts per gradient must be at least 1"),
  beam_size: z
    .number({ invalid_type_error: "Beam size is required" })
    .min(1, "Beam size must be at least 1"),
  num_rounds: z
    .number({ invalid_type_error: "Number of rounds is required" })
    .min(1, "Number of rounds must be at least 1"),
});

const MetaPromptOptimizerSchema = z.object({
  task_description: z.string().optional(),
  num_rounds: z
    .number({ invalid_type_error: "Number of rounds is required" })
    .min(1, "Number of rounds must be at least 1"),
});

const BayesianSearchOptimizerSchema = z
  .object({
    task_description: z.string().optional(),
    min_examples: z
      .number({ invalid_type_error: "Minimum number of examples is required" })
      .min(1, "Minimum examples must be at least 1"),
    max_examples: z
      .number({ invalid_type_error: "Maximum number of examples is required" })
      .min(1, "Maximum examples must be at least 1"),
    n_trials: z
      .number({ invalid_type_error: "Number of trials is required" })
      .min(1, "Number of trials must be at least 1"),
  })
  .refine((data) => data.min_examples < data.max_examples, {
    message: "Min examples must be less than max examples",
    path: ["min_examples"],
  });

const PromptWizardOptimizerSchema = z.object({
  task_description: z.string().optional(),
  mutate_rounds: z
    .number({ invalid_type_error: "Mutated rounds is required" })
    .min(1, "Mutated rounds must be at least 1"),
  refine_iterations: z
    .number({ invalid_type_error: "Refined iterations is required" })
    .min(1, "Refined iterations must be at least 1"),
  beam_size: z
    .number({ invalid_type_error: "Beam size is required" })
    .min(1, "Beam size must be at least 1"),
});

const GEPAOptimizerSchema = z.object({
  task_description: z.string().optional(),
  max_metric_calls: z
    .number({ invalid_type_error: "Max metric calls is required" })
    .min(1, "Max metric calls must be at least 1"),
});

export const createDatasetOptimizationSchema = z.discriminatedUnion(
  "optimizer_algorithm",
  [
    z.object({
      optimizer_algorithm: z.literal("random_search"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: RandomSearchOptimizerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
    z.object({
      optimizer_algorithm: z.literal("protegi"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: ProTeGiOptimzerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
    z.object({
      optimizer_algorithm: z.literal("bayesian"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: BayesianSearchOptimizerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
    z.object({
      optimizer_algorithm: z.literal("metaprompt"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: MetaPromptOptimizerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
    z.object({
      optimizer_algorithm: z.literal("promptwizard"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: PromptWizardOptimizerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
    z.object({
      optimizer_algorithm: z.literal("gepa"),
      name: z.string().min(1, "Name is required"),
      optimizer_model_id: z.string().min(1, "Model is required"),
      column_id: z.string().min(1, "Column is required"),
      optimizer_config: GEPAOptimizerSchema,
      userEvalTemplateIds: z.array(z.any()).optional(),
    }),
  ],
);

// Status values - lowercase to match backend status field values
export const DatasetOptimizationStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
};

// Column definitions for the optimization runs list
export const getOptimizationRunListColumnDef = () => {
  return [
    {
      field: "optimizations",
      headerName: "Optimizations",
      valueGetter: (params) => ({
        title: params.data?.optimizationName,
        startedAt: params.data?.startedAt,
      }),
      flex: 1,
      cellRenderer: "optimizationNameRenderer",
    },
    {
      field: "trial_count",
      headerName: "No. of Trials",
      minWidth: 150,
    },
    {
      field: "optimizer_algorithm",
      headerName: "Optimization Type",
      valueGetter: (params) =>
        KeyOptimizerMapping[params.data?.optimizerAlgorithm],
      minWidth: 200,
    },
    {
      field: "status",
      headerName: "Status",
      cellRenderer: "statusCellRenderer",
      minWidth: 150,
    },
  ];
};

// Column definitions for trials table
export const getTrialsColumnConfig = (columnConfig) => {
  if (!columnConfig || !Array.isArray(columnConfig)) return [];
  return columnConfig.map((column) => {
    switch (column?.id) {
      case "trial":
        return {
          field: "trial",
          headerName: "Trial",
          cellRenderer: "trialCellRenderer",
          minWidth: 200,
          isVisible: true,
          id: "trial",
          colId: "trial",
          valueGetter: (params) => ({
            title: params.data?.trial,
            improvement: params.data?.score_percentage_change,
            isBest: params.data?.is_best,
          }),
        };

      case "prompt":
        return {
          field: "prompt",
          headerName: "Prompts",
          minWidth: 300,
          maxWidth: 400,
          flex: 1,
          id: "prompt",
          colId: "prompt",
          tooltipValueGetter: ({ data }) => data?.prompt,
          isVisible: true,
        };

      default:
        return {
          headerName: column?.name,
          minWidth: 170,
          colId: column?.id,
          cellRenderer: "averageEvalCellRenderer",
          isVisible: true,
          id: column?.id,
          valueGetter: (params) => params.data?.eval_scores?.[column?.id],
        };
    }
  });
};
