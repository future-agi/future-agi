// Who may operate a sandbox, so a code panel never lists a call the stack
// refuses to this person (parity P22 rule). EE's customer sandboxes are
// CREATOR-ONLY (EE 2be31e97 workspace/sandboxes/access/creator-only.ts): only
// the member who created a sandbox may run commands in it, write its files or
// change its state (exec, snapshot, hibernate, resume, ...). Workspace owners
// and admins may also see and destroy every sandbox; every other member
// neither sees nor reaches anyone else's (403, and left out of the list).
//
// Future AGI's session exchange makes an organization Owner an Omega owner, an
// Admin an admin, and an organization Member with a workspace role a member
// (accounts/omega_session.py delegation_role). So a member is only ever shown
// sandboxes they created, and may operate each one. An owner or admin may be
// looking at a sandbox someone else created, and EE's sandbox view names no
// creator (view-extensions.ts CORE_FIELDS at 2be31e97), so the panel cannot
// tell them apart: it fails closed and lists for them the calls that work on
// any sandbox (reads and delete). Unknown (still signing in, no role) is "may
// not". EE checks again either way.

import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { ROLES } from "src/utils/rolePermissionMapping";

// The Future AGI roles the exchange turns into an Omega member. Viewers get no
// Omega session at all, so they are not here (and fail closed if they ever
// reach a page).
const MEMBER_ROLES = new Set([
  ROLES.MEMBER,
  ROLES.WORKSPACE_ADMIN,
  ROLES.WORKSPACE_MEMBER,
]);

/** True when every sandbox this person can see is one they created. */
export const mayOperateSandboxesFor = (role) => MEMBER_ROLES.has(role);

/** Whether this person may run, snapshot, hibernate and resume the sandboxes they see. */
export function useMayOperateSandboxes() {
  const { role } = useSafeAuthContext();
  return mayOperateSandboxesFor(role);
}

/** What a sandbox's Code drawer says when it leaves the creator-only calls out. */
export const CREATOR_ONLY_NOTE =
  "Only the member who created a sandbox can run commands in it, snapshot, hibernate or resume it. As an owner or admin you see every sandbox, so this panel lists the calls that work on any of them: reading and deleting.";
