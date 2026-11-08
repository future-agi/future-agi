import { Helmet } from "react-helmet-async";
import ErrorState from "src/components/error-state";
import { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";

// I composes the adapted SkillEditorSession here. Do not instantiate a second
// draft owner or fall back to leaf-only save/check/publish endpoints.
export default function ContextEditorPage() {
  const composition = useContextAuthoring();
  return <><Helmet><title>Edit agent context</title></Helmet>{(composition?.bindings.editor?.scopeKey === composition?.scopeKey && composition?.bindings.editor?.element) || <ErrorState title="Context editor unavailable" detail="The context-aware shared editing session must be enabled before editing this resource." />}</>;
}
