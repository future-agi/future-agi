import { useDesignReview } from "src/api/simulate-environments/designReview";
import { BUILD_STATUS } from "../../myEnvironments.constants";
import { kept } from "../designReview/designReview.utils";

const hasToolStatus = (env) =>
  Array.isArray(env?.toolStatus) && env.toolStatus.length > 0;

export function withApprovedDesign(env, review) {
  if (!review) return env;
  return {
    ...env,
    rules: env.rules?.length
      ? env.rules
      : kept(review.rules).map((rule) => rule.text),
    useCases: env.useCases?.length
      ? env.useCases
      : kept(review.use_cases).map((useCase) => useCase.text),
    toolStatus: hasToolStatus(env)
      ? env.toolStatus
      : kept(review.tools).map((tool) => ({
          tool: tool.name,
          status: tool.status || "unknown",
          writes: (tool.tables_changed || []).length > 0,
        })),
  };
}

export default function useApprovedDesign(env) {
  const waiting =
    Boolean(env?.designApproved) &&
    env?.buildStatus === BUILD_STATUS.BUILDING &&
    !hasToolStatus(env);
  const { data: review } = useDesignReview(env?.id, { enabled: waiting });
  const shown = waiting ? withApprovedDesign(env, review) : env;
  return { env: shown, fromDesign: waiting && Boolean(review) };
}
