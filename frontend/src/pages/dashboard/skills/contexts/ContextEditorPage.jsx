import { Helmet } from "react-helmet-async";
import ContextAuthoringController from 'src/sections/skills/contexts/ContextAuthoringController';
import { contextAuthoringReady } from 'src/api/skills/context-authoring';
import ErrorState from 'src/components/error-state';
import { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";

// I composes the adapted SkillEditorSession here. Do not instantiate a second
// draft owner or fall back to leaf-only save/check/publish endpoints.
export default function ContextEditorPage() {
  const composition = useContextAuthoring();
  return <><Helmet><title>Edit agent context</title></Helmet>{(composition?.bindings.editor?.scopeKey === composition?.scopeKey && composition?.bindings.editor?.element) || (!composition && contextAuthoringReady() ? <ContextAuthoringController mode="editor" /> : <ErrorState title="Context editor unavailable" detail="A current scoped human-authoring controller is required. No legacy skill operation is substituted." />)}</>;
}
