import { Helmet } from 'react-helmet-async';
import { useSearchParams } from 'react-router-dom';
import ErrorState from 'src/components/error-state';
import ContextLibraryView, { useContextAuthoring } from 'src/sections/skills/contexts/ContextLibraryView';

export default function ContextLibraryPage() {
  const composition = useContextAuthoring();
  const [params] = useSearchParams();
  const binding = composition?.bindings.library;
  return <><Helmet><title>Agent contexts</title></Helmet>{binding?.element || (binding ? <ContextLibraryView {...binding} scopeKey={composition.scopeKey} state={params.get('state')} /> : <ErrorState title="Agent contexts unavailable" detail="The scoped human authoring service is not enabled. Existing Skills are unchanged." />)}</>;
}
