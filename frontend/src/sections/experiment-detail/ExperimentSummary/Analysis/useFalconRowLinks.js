import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

// Falcon's answers are markdown, so its row links are plain <a href> and
// would reload the app. While a Summary is open, a click on a link to this
// dataset's results or Data tab navigates in place instead (the row drawer
// opens from `row=`).
export function isInAppRowLink(href, datasetId) {
  if (!href || !href.startsWith("/dashboard/develop/")) return false;
  if (!/[?&]row=/.test(href)) return false;
  return (
    href.startsWith("/dashboard/develop/experiment/") ||
    (Boolean(datasetId) && href.startsWith(`/dashboard/develop/${datasetId}`))
  );
}

export function useFalconRowLinks(datasetId) {
  const navigate = useNavigate();
  useEffect(() => {
    const onClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey)
        return;
      const a = e.target?.closest?.("a[href]");
      const href = a?.getAttribute("href");
      if (!isInAppRowLink(href, datasetId)) return;
      e.preventDefault();
      navigate(href);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [datasetId, navigate]);
}
