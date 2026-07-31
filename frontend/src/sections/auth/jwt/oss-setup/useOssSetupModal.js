import { useEffect } from "react";

import { useBoolean } from "src/hooks/use-boolean";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// Controls the CLI password-reset fallback.
//
// It never auto-opens on a first visit: signup works in the browser on OSS, so
// there is nothing a new admin needs a shell for. It opens only when asked —
// either from the Forgot Password link, or via ?ossSetup=reset when the route
// guard diverted someone away from /forget-password.
export function useOssSetupModal({ enabled = true, autoOpen = false } = {}) {
  const { isOSS, isLoading, isSuccess, canDeliverEmail } = useDeploymentMode();
  const open = useBoolean(false);
  const openReset = open.onTrue;

  useEffect(() => {
    if (!enabled || isLoading || !isSuccess || !isOSS || !autoOpen) return;
    openReset();
  }, [enabled, isLoading, isSuccess, isOSS, autoOpen, openReset]);

  return {
    isOSS,
    isLoading,
    isSuccess,
    canDeliverEmail,
    open: open.value,
    onClose: open.onFalse,
    openReset,
  };
}
