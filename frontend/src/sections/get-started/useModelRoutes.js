import { useQuery } from "@tanstack/react-query";
import { useApiKeysStatus } from "src/api/model/api-keys";
import { useAuthContext } from "src/auth/hooks";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useManagedModelsStatus } from "src/sections/gateway/models/hooks/useManagedModels";
import { setupReadiness } from "src/sections/gateway/onboarding/setupReadiness";
import axios, { endpoints } from "src/utils/axios";
import { RoutesName, showRoleSpecific } from "src/utils/rolePermissionMapping";

// The organization's custom models (they show as key cards on Add keys).
export function useCustomModels(options = {}) {
  return useQuery({
    queryKey: ["customModals"],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.settings.customModal.getCustomModal,
      );
      return data;
    },
    select: (d) => d?.results || [],
    ...options,
  });
}

// What Get started needs to offer a working way to call a model:
// - creditsOn: the AI Gateway's credits status says this deployment offers
//   Future AGI credits, the organization has them on and its balance is
//   above zero: the gateway's own setup check (setupReadiness, "available,
//   enabled, funded credits"). A used-up balance is refused by the gateway
//   (HTTP 402). Loading, an error, or a missing or unreadable balance counts
//   as off, so no credits route is shown that may not work.
// - noOwnModel: the provider keys and custom models are both read and none
//   is saved. While either loads or fails, nothing is claimed. Only a step
//   that calls a model asks (`ownModelNeeded`), and only once credits are
//   on: with credits off or unknown the page starts no read it did not
//   start before.
// - canAddProviderKey: the role may manage AI provider keys (the Settings
//   AI Providers entry's rule).
// - canCreateGatewayKey: the API keys page's own rule: the organization is
//   loaded and known and the person is an active member, Viewers included.
export function useModelRoutes({ ownModelNeeded = true } = {}) {
  const managed = useManagedModelsStatus();
  const { user } = useAuthContext() ?? {};
  const { orgLevel, currentOrganizationId, isReady } = useOrganization() ?? {};

  const creditsOn =
    managed?.isError !== true &&
    setupReadiness({ managedStatus: managed?.data }).managed === "ready";

  // A query's meta is written by every observer of it, disabled ones
  // included. Add keys reads both lists itself and keeps the app-wide error
  // toast, so errorHandled is passed only by a read this box starts.
  const readOwn = creditsOn && ownModelNeeded;
  const ownReadOptions = readOwn
    ? { enabled: true, meta: { errorHandled: true } }
    : { enabled: false };
  const keys = useApiKeysStatus(ownReadOptions);
  const custom = useCustomModels(ownReadOptions);

  const ownKnown = keys?.isSuccess === true && custom?.isSuccess === true;
  const hasOwnModel =
    (keys?.data || []).some((provider) => provider?.hasKey === true) ||
    (custom?.data || []).length > 0;

  return {
    creditsOn,
    noOwnModel: readOwn && ownKnown && !hasOwnModel,
    canAddProviderKey:
      showRoleSpecific[RoutesName.aiProvider]?.[user?.organization_role] ===
      true,
    canCreateGatewayKey: Boolean(
      isReady &&
        currentOrganizationId &&
        Number.isFinite(orgLevel) &&
        orgLevel >= 1,
    ),
  };
}
