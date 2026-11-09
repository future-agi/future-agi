export const useAuthContext = () => ({
  user: { id: "fixture-person", organization_role: "Member" },
});
export const useSafeAuthContext = useAuthContext;
export const useOrganization = () => ({ currentOrganizationId: "fixture-org" });
export const useWorkspace = () => ({
  currentWorkspaceId: "fixture-workspace",
  currentWorkspaceName: "My workspace",
});
