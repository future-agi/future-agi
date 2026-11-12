import { createContext, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { compareSearch, parseCompareQuery, toggleCompareId } from "./compareQuery";

export const ModelCompareContext = createContext(null);

export function useModelCompare() {
  const location = useLocation();
  const navigate = useNavigate();
  // Draft selections survive catalog filters, but never store catalog/access
  // facts. Shared comparisons take their IDs directly from browser history.
  const [draft, setDraft] = useState([]);
  const shared = useMemo(() => parseCompareQuery(location.search), [location.search]);
  const open = shared.length > 0;
  const ids = open ? shared : draft;
  const changeUrl = (next) => navigate({
    pathname: location.pathname,
    search: compareSearch(location.search, next),
    hash: location.hash,
  }, { state: location.state });
  return {
    ids, open,
    toggle: (id) => {
      const next = toggleCompareId(ids, id);
      setDraft(next);
      if (open) changeUrl(next);
    },
    clear: () => {
      setDraft([]);
      if (open) changeUrl([]);
    },
    show: () => { if (ids.length >= 2) changeUrl(ids); },
    close: () => {
      setDraft(ids);
      changeUrl([]);
    },
  };
}
