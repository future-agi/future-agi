import React, { useEffect, useState } from "react";
import { useAuthContext } from "src/auth/hooks";
import EnterpriseGateDialog from "./EnterpriseGateDialog";
import { ENTERPRISE_GATE_EVENT } from "./enterprise-gate";

/**
 * Opens the Enterprise gate dialog for any refused organization, workspace or
 * member creation: src/utils/axios.js dispatches ENTERPRISE_GATE_EVENT for
 * those 402s instead of a generic error snackbar. Mounted once in app.jsx so
 * sign-up, invites, and workspace and organization creation all get it.
 */
export default function EnterpriseGateHost() {
  const { authenticated } = useAuthContext();
  const [gate, setGate] = useState(null);

  useEffect(() => {
    const onGate = (event) => setGate(event.detail || {});
    window.addEventListener(ENTERPRISE_GATE_EVENT, onGate);
    return () => window.removeEventListener(ENTERPRISE_GATE_EVENT, onGate);
  }, []);

  // Someone signing up (not signed in) cannot activate a license: point them
  // at the people who can add them instead.
  const note =
    !authenticated && gate?.feature === "organizations"
      ? "Already have a teammate on this install? Ask an admin to invite you."
      : undefined;

  return (
    <EnterpriseGateDialog
      open={Boolean(gate)}
      gate={gate || undefined}
      note={note}
      onClose={() => setGate(null)}
    />
  );
}
