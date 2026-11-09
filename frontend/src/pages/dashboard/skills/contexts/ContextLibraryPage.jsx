import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router-dom";
import ContextAuthoringController from 'src/sections/skills/contexts/ContextAuthoringController';
import { contextAuthoringReady } from 'src/api/skills/context-authoring';
import ErrorState from 'src/components/error-state';
import ContextLibraryView, { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";

export default function ContextLibraryPage() {
  const composition = useContextAuthoring();
  const [params] = useSearchParams();
  return <><Helmet><title>Agent contexts</title></Helmet>{composition?.bindings.library ? <ContextLibraryView {...composition.bindings.library} scopeKey={composition.scopeKey} state={params.get("state")} /> : !composition && contextAuthoringReady() ? <ContextAuthoringController mode="library" /> : <ErrorState title="Agent contexts unavailable" detail="The scoped human authoring service is not enabled. Existing Skills are unchanged." />}</>;
}
