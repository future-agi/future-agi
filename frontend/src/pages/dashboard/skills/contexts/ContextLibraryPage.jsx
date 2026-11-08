import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import ErrorState from "src/components/error-state";
import ContextLibraryView, { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";

export default function ContextLibraryPage() {
  const composition = useContextAuthoring();
  const [params] = useSearchParams();
  return <><Helmet><title>Agent contexts</title></Helmet>{composition?.bindings.library ? <ContextLibraryView {...composition.bindings.library} scopeKey={composition.scopeKey} state={params.get("state")} /> : <ErrorState title="Agent contexts unavailable" detail="The scoped authoring service is not enabled. Existing Skills are unchanged." />}</>;
}
