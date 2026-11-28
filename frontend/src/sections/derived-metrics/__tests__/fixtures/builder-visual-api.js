// Synthetic component-visual-test fixtures, not production endpoints.
import { capabilities, catalog, evaluators, id, scope } from "./builder";
export const useAuthContext = () => ({ role: "Owner" });
export const useOrganization = () => ({
  currentOrganizationId: scope.organizationId,
});
export const useWorkspace = () => ({
  currentWorkspaceId: scope.workspaceId,
  currentWorkspaceRole: "workspace_member",
});
export const useMetricsEnabled = () => ({ enabled: true });
export const useDashboardFilterValues = () => ({
  data: [{ value: "fr" }, { value: "en" }],
});
const read = (data) => ({ readState: "ready", data });
export const useMetricCapabilities = () => read(capabilities);
export const useMetricCatalog = () => read(catalog);
export const useMetricEvaluators = () => read(evaluators);
export const useMetricDetail = () => read(null);
const mutation = () => ({ mutateAsync: async () => ({ id }) });
export const useCreateMetric = mutation;
export const useUpdateMetric = mutation;
export const useValidateMetric = mutation;
export const metricsApi = {
  preview: async () => {
    if (new URLSearchParams(location.search).has("unavailable"))
      throw { statusCode: 503 };
    return {
      point_status: "ok",
      unit_noun: "conversations",
      value_format: "percent",
      rate_eligible: true,
      weighted: false,
      summary: "Synthetic component-test preview. Missing inputs are not zero.",
      results: [
        {
          group_key: "",
          status: "ok",
          value: 0.5,
          numerator: 1,
          denominator: 2,
        },
      ],
      sample_units: [
        {
          unit_id: "conversation-1",
          status: "value",
          value_bool: true,
          inputs_bool: { value_1: true },
        },
        {
          unit_id: "conversation-2",
          status: "value",
          value_bool: false,
          inputs_bool: { value_1: false },
        },
        {
          unit_id: "conversation-3",
          status: "no_value",
          no_value_reason: "missing_inputs",
          inputs_bool: { value_1: null },
        },
      ],
    };
  },
};
export const metricErrorState = () => "unavailable";
export const endpoints = {
  project: { listProjects: () => "/synthetic-test-projects" },
};
export default {
  get: async () => ({
    data: { result: { projects: [{ id, name: "Support assistant" }] } },
  }),
};
