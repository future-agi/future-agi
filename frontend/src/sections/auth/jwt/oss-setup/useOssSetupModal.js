import { useCallback, useEffect, useState } from "react";

import { useBoolean } from "src/hooks/use-boolean";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

import { OSS_SETUP_SEEN_KEY, OSS_SETUP_TABS } from "./constants";

export function useOssSetupModal({ enabled = true, autoOpenTab = null } = {}) {
  const { isOSS, isLoading, isSuccess } = useDeploymentMode();
  const open = useBoolean(false);
  const openModal = open.onTrue;
  const [activeTab, setActiveTab] = useState(OSS_SETUP_TABS.CREATE);

  const openCreate = useCallback(() => {
    setActiveTab(OSS_SETUP_TABS.CREATE);
    openModal();
  }, [openModal]);

  const openReset = useCallback(() => {
    setActiveTab(OSS_SETUP_TABS.RESET);
    openModal();
  }, [openModal]);

  useEffect(() => {
    if (!enabled || isLoading || !isSuccess || !isOSS) return;

    const hint = Object.values(OSS_SETUP_TABS).includes(autoOpenTab)
      ? autoOpenTab
      : null;

    // A hint always opens; otherwise auto-open only on the first OSS visit.
    if (!hint) {
      try {
        if (localStorage.getItem(OSS_SETUP_SEEN_KEY)) return;
        localStorage.setItem(OSS_SETUP_SEEN_KEY, "1");
      } catch {
        return;
      }
    } else {
      try {
        localStorage.setItem(OSS_SETUP_SEEN_KEY, "1");
      } catch {
        /* ignore */
      }
    }

    if (hint === OSS_SETUP_TABS.RESET) openReset();
    else openCreate();
  }, [
    enabled,
    isLoading,
    isSuccess,
    isOSS,
    autoOpenTab,
    openCreate,
    openReset,
  ]);

  return {
    isOSS,
    isLoading,
    isSuccess,
    open: open.value,
    activeTab,
    setActiveTab,
    onClose: open.onFalse,
    openCreate,
    openReset,
  };
}
