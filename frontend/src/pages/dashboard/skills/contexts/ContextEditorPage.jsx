import { Helmet } from 'react-helmet-async';
import ErrorState from 'src/components/error-state';
import { useContextAuthoring } from 'src/sections/skills/contexts/ContextLibraryView';

export default function ContextEditorPage() {
  const composition = useContextAuthoring();
  return <><Helmet><title>Edit agent context</title></Helmet>{(composition?.bindings.editor?.scopeKey === composition?.scopeKey && composition?.bindings.editor?.element) || <ErrorState title="Context editor unavailable" detail="A current scoped human-authoring controller is required. No legacy skill operation is substituted." />}</>;
}
