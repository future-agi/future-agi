import { useSearchParams } from "react-router-dom";

// Viewing an older environment version, held in ?viewAs= so it survives a
// reload and has one way back. Read-only while set. Environment versions are
// sample data today, so callers only honour it with ?uiSample=versions.
export default function useEnvViewAs() {
  const [params, setParams] = useSearchParams();
  const viewAs = params.get("viewAs") || null;
  const setViewAs = (label) => {
    const q = new URLSearchParams(params);
    if (label) q.set("viewAs", label);
    else q.delete("viewAs");
    setParams(q, { replace: true });
  };
  return { viewAs, setViewAs, clearViewAs: () => setViewAs(null) };
}
