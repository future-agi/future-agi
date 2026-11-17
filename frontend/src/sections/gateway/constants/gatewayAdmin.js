// The gateway admin role check's level and label (useGatewayAdmin), in a
// module with no imports, so a control can show the label without loading
// the organization context.
export const GATEWAY_ADMIN_LEVEL = 8;
export const GATEWAY_ADMIN_ONLY = "Owner or Admin only";

// The workspace write check's level and label (useWorkspaceWrite): Member and
// above write to a workspace.
export const WORKSPACE_WRITE_LEVEL = 3;
export const WORKSPACE_WRITE_ONLY = "Owner, Admin or Member only";
