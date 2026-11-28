import { Helmet } from 'react-helmet-async';
import { useSearchParams } from 'react-router-dom';
import CapabilityNotice from 'src/sections/skills/components/CapabilityNotice';
import ContextLibraryView, { useContextAuthoring } from 'src/sections/skills/contexts/ContextLibraryView';

export default function ContextLibraryPage() {
  const composition = useContextAuthoring();
  const [params] = useSearchParams();
  const binding = composition?.bindings.library;
  return <><Helmet><title>Agent contexts</title></Helmet>{binding?.element || (binding ? <ContextLibraryView {...binding} scopeKey={composition.scopeKey} state={params.get('state')} /> : <CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="Existing skills stay available. Ask a workspace admin about access." />)}</>;
}
