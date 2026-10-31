import { useSearchParams } from "react-router-dom";
import {
  BUILD_DRAWER_TAB,
  ENTRY_TAB,
  DEFAULT_ENTRY_TAB,
} from "../environmentOptions";

export default function useEnvironmentsTab() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab = Object.values(ENTRY_TAB).includes(raw) ? raw : DEFAULT_ENTRY_TAB;

  const setTab = (next) => {
    const q = new URLSearchParams(params);
    q.set("tab", next);
    setParams(q, { replace: true });
  };

  return { tab, setTab, opensBuild: raw === BUILD_DRAWER_TAB };
}
