import { useSearchParams } from "react-router-dom";
import { WORKSPACE_TABS } from "../workspace.constants";

const TAB_IDS = WORKSPACE_TABS.map((t) => t.id);
// The summary is the landing tab, even though the rail now orders it after the
// setup tabs — not the first tab in the array.
const DEFAULT_TAB = "summary";

// The route workspace keeps the active tab in `?tab=` (the Phase-1 convention),
// so a deep link and a refresh land on the same panel. Clone of
// hooks/useEnvironmentsTab over the five workspace tabs; an unknown value falls
// back to the summary.
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
