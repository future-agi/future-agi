import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { variablesOf } from "src/sections/common/EvalTypes/dagModel";

/** Child eval inputs are resolved by the scoped backend, including in drafts. */
export default function useDagInputs(dag) {
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  const hasEval = dag?.nodes?.some((node) => node.kind === "eval");
  const query = useQuery({
    queryKey: [
      "evals",
      "dag-inputs",
      currentOrganizationId,
      currentWorkspaceId,
      dag,
    ],
    queryFn: async () => {
      const { data } = await axios.post(
        apiPath("/model-hub/eval-templates/dag/inputs/"),
        { dag },
      );
      return data.result.required_keys;
    },
    enabled: Boolean(hasEval),
    retry: false,
  });
  const reasons = !hasEval
    ? []
    : query.isError
      ? [
          query.error?.response?.data?.errors?.join(" · ") ||
            "Could not resolve eval inputs",
        ]
      : query.isFetching || !query.data
        ? ["Loading eval inputs"]
        : [];
  const vars = [
    ...new Set(hasEval ? query.data ?? [] : variablesOf(dag)),
  ].filter((v) => v !== "output");
  return { vars, reasons };
}
