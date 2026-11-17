import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useRunConfigs } from 'src/api/develop/r2';
import { useGetExperimentDetails } from 'src/hooks/useGetExperimentDetails';
import axios from 'src/utils/axios';
import ExperimentSourceGate from 'src/sections/develop-detail/Experiment/ExperimentSourceGate';

// Test-only entry: never imported by a product route or the product build.
axios.defaults.headers.common.Authorization = `Bearer ${localStorage.getItem('accessToken')}`;
axios.defaults.headers.common['X-Organization-Id'] = sessionStorage.getItem('organizationId');
axios.defaults.headers.common['X-Workspace-Id'] = sessionStorage.getItem('workspaceId');
// Match app.jsx defaults; the production hook supplies the failure policy.
const client = new QueryClient({ defaultOptions: { queries: {
  staleTime: 5_000, gcTime: 60_000, retry: 1,
  refetchOnWindowFocus: true, refetchOnMount: true, refetchOnReconnect: true,
} } });
const params = new URLSearchParams(location.search);
const datasetId = params.get('datasetId');
const experimentId = params.get('experimentId');

function Source() {
  const detail = useGetExperimentDetails(experimentId);
  const configs = useRunConfigs(datasetId);
  return <section aria-label="Experiment source">
    <output aria-label="Configuration request state">{configs.fetchStatus}</output>
    <output aria-label="Experiment identity">{detail.data?.id || ''}</output>
    <ExperimentSourceGate detail={detail} configs={configs}>
      <p>Editable source ready</p>
    </ExperimentSourceGate>
  </section>;
}
function Replay() {
  const [mounted, setMounted] = useState(true);
  return <>
    <h1>Isolated component replay</h1>
    <p>Not public-route acceptance</p>
    <button onClick={() => setMounted(value => !value)}>
      {mounted ? 'Unmount source' : 'Mount source'}
    </button>
    {mounted && <Source />}
  </>;
}
createRoot(document.getElementById('root')).render(
  <QueryClientProvider client={client}><Replay /></QueryClientProvider>,
);
