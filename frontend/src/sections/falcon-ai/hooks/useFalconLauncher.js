import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useFeatureAllowed } from "src/hooks/useCapabilities";
import useFalconStore from "../store/useFalconStore";

// What a Falcon AI launcher does, wherever it is placed (the floating button,
// or the button in the Omega mobile bar): open the sidebar, or the Falcon AI
// page with its capability upsell when the feature is not allowed. A launcher
// is hidden on the Falcon AI page itself and while the sidebar is open.
export default function useFalconLauncher() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const isSidebarOpen = useFalconStore((s) => s.isSidebarOpen);
  const toggleSidebar = useFalconStore((s) => s.toggleSidebar);

  const openFalconAI = useCallback(() => {
    if (!falconAllowed) {
      // Full page shows the capability upsell for unlicensed deployments.
      navigate("/dashboard/falcon-ai");
      return;
    }
    toggleSidebar();
  }, [falconAllowed, navigate, toggleSidebar]);

  const hidden = pathname.startsWith("/dashboard/falcon-ai") || isSidebarOpen;

  return { openFalconAI, hidden };
}
