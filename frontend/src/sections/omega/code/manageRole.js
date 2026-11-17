// Who may change a deployment, so a code panel never shows a write the stack
// refuses to this person (parity P22 rule). EE's deploy, rollback,
// access-token and delete routes call context.manage(...), which admits the
// workspace's MANAGING_ROLES (owner, admin) and answers anyone else 403
// role_forbidden (EE 2be31e97 project-deployments routes/index.ts). Future
// AGI's session exchange makes an organization Owner an Omega owner, an Admin
// an admin and every other role a member (accounts/omega_session.py
// delegation_role). The vault's writes follow the same rule, so this reuses
// the Secrets page's own check and the two can never disagree. Unknown (still
// signing in, no role) is "may not": a panel fails closed.

import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";

import { canManageSecretsFor } from "../deployments/lib/secretForm";

export const canManageDeploymentsFor = (role) => canManageSecretsFor(role);

/** Whether this person may deploy, roll back, mint a token or delete (EE checks again). */
export function useCanManageDeployments() {
  const { role } = useSafeAuthContext();
  return canManageDeploymentsFor(role);
}
