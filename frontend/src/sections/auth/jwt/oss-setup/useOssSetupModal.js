import { useEffect, useState } from "react";

import { useBoolean } from "src/hooks/use-boolean";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

import { OSS_SETUP_SEEN_KEY, OSS_SETUP_TABS } from "./constants";

export function useOssSetupModal({ enabled = true, autoOpenTab = null } = {}) {
  const { isOSS, isLoading } = useDeploymentMode();
  const open = useBoolean(false);
  const [activeTab, setActiveTab] = useState(OSS_SETUP_TABS.CREATE);

  const openCreate = () => {
    setActiveTab(OSS_SETUP_TABS.CREATE);
    open.onTrue();
  };

  const openReset = () => {
    setActiveTab(OSS_SETUP_TABS.RESET);
    open.onTrue();
  };

  useEffect(() => {
    if (!enabled || isLoading || !isOSS) return;

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, isLoading, isOSS, autoOpenTab]);

  return {
    isOSS,
    isLoading,
    open: open.value,
    activeTab,
    setActiveTab,
    onClose: open.onFalse,
    openCreate,
    openReset,
  };
}
