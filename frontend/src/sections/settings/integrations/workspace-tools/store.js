import { useOrganization } from "src/contexts/OrganizationContext";
import {
  setIntegrationsScope,
  useIntegrations as useUnscopedIntegrations,
} from "./mockStore";

/* Everything the mock store offers, with useIntegrations scoped to the current organization. */
export * from "./mockStore";

export const useIntegrations = () => {
  const { currentOrganizationId } = useOrganization();
  setIntegrationsScope(currentOrganizationId, { silent: true });
  return useUnscopedIntegrations();
};
