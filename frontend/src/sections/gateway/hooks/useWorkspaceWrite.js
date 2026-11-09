import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import {
  WORKSPACE_WRITE_LEVEL,
  WORKSPACE_WRITE_ONLY,
} from "../constants/gatewayAdmin";
import { isGatewayAdminLevel } from "./useGatewayAdmin";

// The workspace write check (futureagi/accounts User.can_write_to_workspace):
// an organization Owner or Admin writes to every workspace, and anyone else
// to a workspace where their role is Member or above. A console control that
// writes workspace data (closing a session) reads this hook, and for a Viewer
// shows disabled with WORKSPACE_WRITE_ONLY. The current workspace's level
// comes from the workspace context; the org level stands in until it loads.
// Levels are tfc/constants/levels.py's: Viewer 1, Member 3, Admin 8, Owner 15.

export function canWriteWorkspace(orgLevel, wsLevel) {
  if (isGatewayAdminLevel(orgLevel)) return true;
  const level = typeof wsLevel === "number" ? wsLevel : orgLevel;
  return typeof level === "number" && level >= WORKSPACE_WRITE_LEVEL;
}

export function useWorkspaceWrite() {
  const { orgLevel } = useOrganization();
  const { wsLevel } = useWorkspace();
  const canWrite = canWriteWorkspace(orgLevel, wsLevel);
  return { canWrite, disabledReason: canWrite ? null : WORKSPACE_WRITE_ONLY };
}
