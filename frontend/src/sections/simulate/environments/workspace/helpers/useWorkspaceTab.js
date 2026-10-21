import { useSearchParams } from "react-router-dom";
import { WORKSPACE_TABS } from "../workspace.constants";

const TAB_IDS = WORKSPACE_TABS.map((t) => t.id);
// Overview is the landing tab and the first tab in the rail.
const DEFAULT_TAB = "overview";

// The route workspace keeps the active tab in `?tab=` (the Phase-1 convention),
// so a deep link and a refresh land on the same panel. Clone of
// hooks/useEnvironmentsTab over the workspace tabs; an unknown value falls
// back to the overview.
export default function useWorkspaceTab() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab = TAB_IDS.includes(raw) ? raw : DEFAULT_TAB;

  const setTab = (next) => {
    const q = new URLSearchParams(params);
    q.set("tab", next);
    setParams(q, { replace: true });
  };

  return { tab, setTab };
}
