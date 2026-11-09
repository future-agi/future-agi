import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { isSurfaceShown } from "src/launch/launchSwitch";

export function juryInputKeys(definition) {
  const seats = definition?.seats || [];
  return [
    ...(seats.some((seat) => seat.level === "step") ? ["steps"] : []),
    ...(seats.some((seat) => seat.kind === "check") ? ["checks"] : []),
  ];
}

// A mapping form's variables with the jury's structured inputs added. With
// none to add (the switch OFF, or no step or check seat) the form keeps the
// exact variables array it had without the jury.
export function withJuryInputs(variables, juryInputs) {
  return juryInputs.length
    ? [...new Set([...variables, ...juryInputs])]
    : variables;
}

export default function useJuryDatasetInputs(
  templateId,
  enabled,
  versionId,
  variables,
  bindingConfig,
  setVersion,
  savedMapping,
) {
  const savedVersionId = bindingConfig?.jury_version_id;
  const bindingKey = JSON.stringify([templateId, savedVersionId]);
  const [hydratedBinding, setHydratedBinding] = useState(null);
  // Resolve the saved pin synchronously, before an old query can prune mappings.
  // After hydration, an explicit user selection (including Default) takes over.
  const hydrating = hydratedBinding !== bindingKey;
  const resolvedVersionId =
    hydrating && savedVersionId ? savedVersionId : versionId;
  useEffect(() => {
    if (!hydrating) return;
    if (savedVersionId) setVersion(savedVersionId);
    setHydratedBinding(bindingKey);
  }, [bindingKey, hydrating, savedVersionId, setVersion]);
  const definition = useQuery({
    queryKey: ["jury-dataset-inputs", templateId, resolvedVersionId],
    queryFn: async () =>
      (
        await axios.get(
          apiPath(
            "/model-hub/eval-templates/{template_id}/composite/jury/config/",
            { template_id: templateId },
          ),
          {
            params: resolvedVersionId ? { version_id: resolvedVersionId } : {},
          },
        )
      ).data.result,
    enabled: Boolean(templateId && enabled && isSurfaceShown("evals.jury")),
    placeholderData: (previous) => previous,
  });
  // Do not prune a reopened binding while its immutable definition loads.
  const keys =
    definition.data && !definition.isPlaceholderData
      ? juryInputKeys(definition.data)
      : enabled && isSurfaceShown("evals.jury")
        ? Object.keys(savedMapping || {}).filter((key) =>
            ["steps", "checks"].includes(key),
          )
        : [];
  return [...new Set([...variables, ...keys])];
}
