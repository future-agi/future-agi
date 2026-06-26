import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { extractVariables } from "src/utils/utils";
import { z } from "zod";

export const useFunctionEvalsList = () => {
  return useQuery({
    queryKey: ["function-evals-list"],
    queryFn: () => axios.get(endpoints.develop.eval.getFunctionEvalsList),
    select: ({ data }) => data.result.functions,
  });
};

export const EvalTypeId = Object.freeze({
  DETERMINISTIC: "DeterministicEvaluator",
  CUSTOM_PROMPT: "CustomPromptEvaluator",
});

function getAppropriateField(type) {
  switch (type) {
    case "integer":
      return z.number();
    case "list":
      return z
        .array(z.object({ value: z.string() }))
        .min(1, "Add atleast 1 value")
        .transform((arr) => arr.map((item) => item.value));
    case "boolean":
      return z.boolean();
    case "dict":
      return z
        .string()
        .min(1, "Please enter a valid JSON object")
        .refine(
          (val) => {
            try {
              JSON.parse(val);
              return true;
            } catch {
              return false;
            }
          },
          {
            message:
              "The JSON provided is incorrect. Please verify their structure, format, and content before resubmitting.",
          },
        )
        .transform((val) => {
          try {
            return JSON.parse(val);
          } catch {
            return val; // Return the original string if parsing fails
          }
        });
    default:
      return z.string().min(1, "This field is required");
  }
}

export function buildFormSchema(functionEvalsList) {
  const staticFields = z
    .object({
      templateType: z.string().min(1, "This field is required"),
      templateId: z.string().optional(),
      name: z
        .string()
        .min(1, "This field is required")
        .regex(
          /^[a-z0-9_-]+$/,
          "Only lowercase letters, numbers, underscores, and hyphens are allowed (no spaces)",
        ),
      criteria: z.string().optional(),
      outputType: z.string().min(1, "This field is required"),
      choices: z
        .array(
          z.object({
            key: z.string(),
            value: z.string(),
          }),
        )
        .optional(),
      multiChoice: z.boolean(),
      tags: z
        .array(
          z.object({
            key: z.string(),
            value: z.string(),
          }),
        )
        .optional(),
      requiredKeys: z.array(z.string()).optional(),
      description: z.string().optional(),
      // checkInternet: z.boolean().optional(),
    })
    .superRefine((data, ctx) => {
      if (
        data.outputType === "choices" &&
        (!data.choices || data.choices.length === 0)
      ) {
        ctx.addIssue({
          path: ["choices"],
          code: z.ZodIssueCode.custom,
          message: "Add atleast one choice",
        });
      }
      if (data.templateType !== "Function") {
        if (data.criteria.length === 0) {
          ctx.addIssue({
            path: ["criteria"],
            code: z.ZodIssueCode.custom,
            message: "This field is required",
          });
        }
        if (extractVariables(data.criteria).length === 0) {
          ctx.addIssue({
            path: ["criteria"],
            code: z.ZodIssueCode.custom,
            message: "Add atleast one variable",
          });
        }
      }
    });

  const fieldsArray = [
    z.object({
      evalTypeId: z.enum([
        "",
        "CustomPromptEvaluator",
        "DeterministicEvaluator",
      ]),
      config: z.object({
        model: z.string(),
        reverseOutput: z.boolean(),
      }),
    }),
  ];
  const seenEvalTypeIds = new Set([
    "",
    "CustomPromptEvaluator",
    "DeterministicEvaluator",
  ]);
  for (let i = 0; i < functionEvalsList.length; i++) {
    const element = functionEvalsList[i];
    const evalTypeId = element?.config?.evalTypeId;
    const config = element?.config?.config;
    if (!evalTypeId || seenEvalTypeIds.has(evalTypeId) || !config) {
      continue;
    }
    seenEvalTypeIds.add(evalTypeId);
    const configKeys = Object.keys(config);
    const configMapping = {};
    for (let j = 0; j < configKeys.length; j++) {
      const key = configKeys[j];
      configMapping[key] = getAppropriateField(config[key]["type"]);
    }
    const validationObject = z.object({
      evalTypeId: z.literal(evalTypeId),
      config: z.object({
        config: z.object({ ...configMapping }),
      }),
    });
    fieldsArray.push(validationObject);
  }

  const dynamicFields = z.discriminatedUnion("evalTypeId", fieldsArray);

  return dynamicFields.and(staticFields).superRefine((data, ctx) => {
    if (data.evalTypeId === "LengthBetween") {
      if (data.config["config"].minLength > data.config["config"].maxLength) {
        ctx.addIssue({
          path: ["config.config.minLength"],
          message: "Min length cannot be greater than Max length",
          code: z.ZodIssueCode.custom,
        });
      }
    }
    if (data.templateType === "Function") {
      if (data.evalTypeId === "") {
        ctx.addIssue({
          path: ["evalTypeId"],
          message: "This field is required",
          code: z.ZodIssueCode.custom,
        });
      }
    } else {
      if (data.config.model === "") {
        ctx.addIssue({
          path: ["config.model"],
          message: "This field is required",
          code: z.ZodIssueCode.custom,
        });
      }
    }
  });
}

export function getUniqueEvalRequiredKeys(evals) {
  const allKeys = evals.flatMap(
    (evalItem) => evalItem.eval_required_keys || [],
  );
  return [...new Set(allKeys)];
}

export const evalDrawerTabs = [
  {
    label: "Evals",
    value: "evals",
    disabled: false,
    navigateRoute: "/dashboard/evaluations",
  },
  {
    label: "Groups",
    value: "groups",
    disabled: false,
  },
];

export const EVALUATION_PAGE_ID_MAPPER = {
  task: "EVAL_TASK",
  workbench: "PROMPT",
  dataset: "DATASET",
  simulate: "SIMULATE",
  "run-experiment": "DATASET",
  "run-optimization": "DATASET",
  experiment: "EXPERIMENT",
  "create-experiment": "EXPERIMENT",
  // ... add other mappings as needed
};

export function getUpdatedRequiredKeys(groupData, removedEvals = []) {
  if (!groupData?.result?.members) return [];

  const members = groupData.result.members;
  const removedSet = new Set(removedEvals);

  // Count how many *active* members use each key
  const keyUsageCount = {};

  for (const member of members) {
    const isRemoved = removedSet.has(member.eval_template_id);

    for (const key of member.requiredKeys || []) {
      // Only count usage if member is NOT being removed
      if (!isRemoved) {
        keyUsageCount[key] = (keyUsageCount[key] || 0) + 1;
      }
    }
  }

  // Now filter requiredKeys:
  // keep if key is still used OR it wasn’t from a removed member at all
  const resultKeys = [];

  for (const key of groupData.result.requiredKeys) {
    if (keyUsageCount[key] > 0) {
      resultKeys.push(key);
    }
  }

  return resultKeys;
}

export function getCommonFutureEvalModels(groupData, removedEvals = []) {
  if (!groupData?.result?.members) return [];

  const removedSet = new Set(removedEvals);

  // Filter members: not removed and has FUTURE_EVALS tag
  const futureMembers = groupData.result.members.filter(
    (member) =>
      !removedSet.has(member.eval_template_id) &&
      member.tags?.includes("FUTURE_EVALS") &&
      Array.isArray(member.models),
  );

  if (futureMembers.length === 0) return [];

  // Start with the models of the first member
  let commonModels = new Set(futureMembers[0].models);

  // Keep only models present in all other members
  for (let i = 1; i < futureMembers.length; i++) {
    const memberModelsSet = new Set(futureMembers[i].models);
    const commonArray = Array.from(commonModels); // convert set to array
    commonModels = new Set(commonArray.filter((m) => memberModelsSet.has(m)));
    if (commonModels.size === 0) break; // early exit if no intersection
  }

  return Array.from(commonModels); // convert final set to array
}

export function getMemberNamesForKey(members, key) {
  if (!Array.isArray(members) || !key)
    return {
      displayString: "",
      remaining: [],
    };

  const names = [];

  for (const member of members) {
    if ((member.requiredKeys || []).includes(key)) {
      names.push(member.name);
    }
  }

  return {
    displayString: names.slice(0, 3).join(", "),
    remaining: names.slice(3, -1),
  };
}

export function groupEvalsByRequiredKeys(members = []) {
  const requiredKeys = members.map((m) => m.requiredKeys);
  const optionalKeys = members
    ?.filter((item) => item?.optionalKeys)
    .flatMap((item) => item?.optionalKeys)
    ?.map((item) => `OPT+${item}`);
  const flattenedUniqueKeys = [
    ...new Set(requiredKeys.flat()),
    ...new Set(optionalKeys),
  ];
  const hashMap = {};

  function arraysEqualIgnoreOrder(arr1, arr2) {
    if (!arr1 || !arr2) return false;
    if (arr1.length !== arr2.length) return false;
    const sorted1 = [...arr1].sort();
    const sorted2 = [...arr2].sort();
    return sorted1.every((val, idx) => val === sorted2[idx]);
  }

  function canCreateGroup(keys) {
    let foundRelevantMember = false;

    for (const member of members) {
      if (!member?.requiredKeys) continue;
      const optionalKeys =
        member?.optionalKeys?.map((item) => `OPT+${item}`) ?? [];
      const hasAnyKey = keys.some((k) =>
        [...member.requiredKeys, ...optionalKeys].includes(k),
      );
      if (hasAnyKey) {
        foundRelevantMember = true;
        if (
          !arraysEqualIgnoreOrder(
            [...member.requiredKeys, ...optionalKeys],
            keys,
          )
        ) {
          return false;
        }
      }
    }
    return foundRelevantMember;
  }

  const groupCreatedFor = [];

  for (const member of members) {
    const optionalKeys =
      member?.optionalKeys?.map((item) => `OPT+${item}`) ?? [];
    const sortedKeys = [...member.requiredKeys, ...optionalKeys].sort();
    if (canCreateGroup(sortedKeys)) {
      const key = sortedKeys.join("+");
      if (!hashMap[key]) {
        hashMap[key] = { requiredKeys: sortedKeys, evals: [] };
      }

      // only push if eval_template_id not already in this group
      if (
        !hashMap[key].evals.some(
          (e) => e.eval_template_id === member.eval_template_id,
        )
      ) {
        hashMap[key].evals.push(member);
      }

      groupCreatedFor.push(...sortedKeys);
    }
  }

  const filteredKeys = flattenedUniqueKeys.filter(
    (key) => !groupCreatedFor.includes(key),
  );

  for (const fKey of filteredKeys) {
    if (!hashMap[fKey]) {
      hashMap[fKey] = { requiredKeys: [fKey], evals: [] };
    }
  }

  for (const member of members) {
    const optionalKeys =
      member?.optionalKeys?.map((item) => `OPT+${item}`) ?? [];
    for (const rk of [...member.requiredKeys, ...optionalKeys]) {
      if (rk in hashMap) {
        if (
          !hashMap[rk].evals.some(
            (e) => e.eval_template_id === member.eval_template_id,
          )
        ) {
          hashMap[rk].evals.push(member);
        }
      }
    }
  }

  return Object.values(hashMap);
}

export const ADD_BUTTON_MAPPER = {
  "update-experiment": "Update",
};

export const ADD_AND_RUN_BUTTON_MAPPER = {
  "update-experiment": "Update and Run",
};

// Eval Name Utilities
export const DEFAULT_EVAL_MODEL = "turing_large";

/**
 * Get base name for an eval from its config
 * @param {Object} evalConfig - The eval configuration object
 * @returns {string} The base name for the eval
 */
export function getEvalBaseName(evalConfig) {
  return (
    evalConfig?.name ||
    evalConfig?.eval_name ||
    evalConfig?.template_name ||
    "Evaluation"
  );
}

/**
 * Generate a unique eval name by appending numbers if duplicates exist
 * @param {string} baseName - The base name to start with
 * @param {Array} existingEvals - Array of existing eval objects
 * @returns {string} A unique name (e.g., "eval_name", "eval_name 2", "eval_name 3")
 */
export function generateUniqueEvalName(baseName, existingEvals = []) {
  if (!baseName) return "Evaluation";

  // Get list of existing eval names (case-insensitive)
  const existingNames = (existingEvals || [])
    .map((e) => {
      const name = e?.name || e?.eval_name || e?.template_name || "";
      return name.toLowerCase();
    })
    .filter(Boolean);

  // If base name doesn't exist, use it as is
  if (!existingNames.includes(baseName.toLowerCase())) {
    return baseName;
  }

  // Find the next available number
  let counter = 2;
  let newName = `${baseName} ${counter}`;
  while (existingNames.includes(newName.toLowerCase())) {
    counter++;
    newName = `${baseName} ${counter}`;
  }
  return newName;
}

/**
 * Get the preferred model value, defaulting to turing_large if available
 * @param {Array} modelOptions - Array of available model option values
 * @param {string} currentValue - Current/default value
 * @param {boolean} editMode - Whether in edit mode (preserve existing value)
 * @returns {string} The model value to use
 */
export function getPreferredModelValue(
  modelOptions = [],
  currentValue = "",
  editMode = false,
) {
  if (editMode && currentValue) {
    return currentValue;
  }

  const hasTuringLarge = modelOptions.includes(DEFAULT_EVAL_MODEL);
  if (hasTuringLarge) {
    return DEFAULT_EVAL_MODEL;
  }

  return currentValue || modelOptions[0] || "";
}

/**
 * Validates that at least one mapping field references the required column.
 * Returns an error value (string | object) when validation fails, or null on success.
 */
export function validateRequiredColumnMapping({
  requiredColumnIds,
  allColumns,
  mapping,
  isGroupEvals,
  groupedRequiredKeys,
  filteredRequiredKeys,
}) {
  if (!requiredColumnIds) return null;

  const columnLabel =
    allColumns?.find((col) => (col?.field || col?.id) === requiredColumnIds)
      ?.headerName ?? null;

  const buildMessage = (fallback) =>
    columnLabel
      ? `At least one field must be mapped to the column "${columnLabel}"`
      : fallback;

  if (isGroupEvals) {
    const groupErrors = {};

    groupedRequiredKeys?.forEach((group, index) => {
      const groupKeys = group?.requiredKeys?.filter((k) =>
        filteredRequiredKeys?.includes(k),
      );
      if (!groupKeys?.length) return;

      const hasRequiredColumn = groupKeys.some(
        (key) => mapping[key] === requiredColumnIds,
      );
      if (!hasRequiredColumn) {
        groupErrors[index] = buildMessage(
          `At least one field must be mapped to the input column`,
        );
      }
    });

    if (Object.keys(groupErrors).length > 0) return groupErrors;
  } else {
    const mappingValues = Object.values(mapping);
    if (!mappingValues.includes(requiredColumnIds)) {
      return buildMessage(`At least one field must use the input column`);
    }
  }

  return null;
}

// Bundle Evals Utilities
export function getBundleRequiredKeys(evalsList, removedEvalIds = []) {
  if (!Array.isArray(evalsList)) return [];

  const removedSet = new Set(removedEvalIds);
  const activeEvals = evalsList.filter(
    (evalItem) => !removedSet.has(evalItem.eval_template_id),
  );

  // Collect all required keys from active evals
  const allKeys = activeEvals.flatMap(
    (evalItem) => evalItem.requiredKeys || [],
  );

  // Return unique keys
  return [...new Set(allKeys)];
}
