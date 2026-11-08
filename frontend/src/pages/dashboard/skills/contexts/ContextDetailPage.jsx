import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import ErrorState from "src/components/error-state";
import { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";
import ContextDetailView from "src/sections/skills/contexts/ContextDetailView";

export default function ContextDetailPage() {
  const composition = useContextAuthoring();
  const [params] = useSearchParams();
  return <><Helmet><title>Agent context</title></Helmet>{composition?.bindings.detail ? <ContextDetailView {...composition.bindings.detail} scopeKey={composition.scopeKey} state={params.get("state")} /> : <ErrorState title="Context unavailable" detail="Select an authorized context through the enabled authoring service." />}</>;
}
