import { useEffect, useState } from "react";
import {
  awaitedCheck,
  useEnvironmentVersions,
} from "src/api/simulate-environments/versions";
import { checkMissing } from "../../agents/agentVersionStatus";

// The agent version whose missing values Ready to run asks for, and the version they were saved as, followed until
// its check lands. New values are saved over the default version only, so only the default's are asked for.
export default function useMissingCredentials(
  envId,
  agentId,
  picksKey,
  enabled,
) {
  const agents = useEnvironmentVersions(envId, { enabled }).data?.agent || [];
  const [savedId, setSavedId] = useState(null);
  useEffect(() => setSavedId(null), [picksKey]);
  const version = enabled && agents.find((one) => one.id === agentId);
  const saved = (savedId && agents.find((one) => one.id === savedId)) || null;
  return {
    asked: version?.isDefault && checkMissing(version).length ? version : null,
    saved,
    checking: Boolean(saved && awaitedCheck(saved)),
    onSaved: (data) => setSavedId(data?.agent_version?.id || null),
  };
}
