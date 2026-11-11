import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import { useContextTestSuite, useSaveContextTestSuite } from 'src/api/skills/context-testing';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import Section from 'src/components/section';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import TestCaseEditor from '../tests/TestCaseEditor';

// Intent: incident responders inspect retained cases without mistaking a text
// answer for a repaired application. Historical identity remains visible.
// Hierarchy: viewed release, native limitation, then reviewed requests.
// Palette/depth/surfaces: neutral, flat table with the shared warning role.
// Typography/spacing: m3/s1/s3, 12px groups and standard dense table rows.
function Tests({ contextId, version, canWrite }) {
  const suite = useContextTestSuite(contextId);
  const save = useSaveContextTestSuite(contextId);
  const [editor, setEditor] = useState(null);
  const [error, setError] = useState('');
  if (suite.isLoading) return <ContentSkeleton label="Loading context regression cases" />;
  if (suite.isError) return <ErrorState title="Could not load context regression cases" onRetry={suite.refetch} />;
  if (!suite.data) return <EmptyState title="Select an authenticated workspace" description="Regression cases remain scoped to their context and workspace." />;
  const changed = editor && editor.revision !== suite.data.revision;
  return <Stack spacing={1.5}>
    <Typography variant="m3">Context regression cases{version ? ` · viewed v${version.number}` : ''}</Typography>
    <Alert severity="warning">Application verification unavailable. Captured text is not a reproduced historical failure or release-ready native evidence. The hosted application launch and authenticated native result ingestion are not connected in this deployment.</Alert>
    <Typography variant="s1">To capture an existing failure, open its exact span in Observe and choose Create context regression case. Its physical identity and selected request/tool text are carried into review; do not retype them here.</Typography>
    <Section title="Reviewed examples">
      {!suite.data.cases.length ? <EmptyState title="No regression cases yet" description="Capture a retained trace, then review its redaction and expected behavior. Working neighboring cases belong in the same suite." /> : <TableContainer>
        <Table size="small" aria-label="Context regression cases"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Example</TableCell><TableCell>Review</TableCell><TableCell>Checks</TableCell><TableCell>Action</TableCell></TableRow></TableHead>
          <TableBody>{suite.data.cases.map((item) => <TableRow key={item.id} hover sx={{ height: ROW_HEIGHT.default }}>
            <TableCell><Stack spacing={0.5}><Typography component="div" variant="s1">{item.title}</Typography><Typography component="div" variant="s3" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.prompt}</Typography></Stack></TableCell>
            <TableCell>{item.reviewed ? 'Reviewed' : 'Needs review'}</TableCell><TableCell>{item.checks.length}</TableCell>
            <TableCell><Button size="small" variant="outlined" disabled={!canWrite} onClick={() => { setError(''); setEditor({ value: item, revision: suite.data.revision, cases: suite.data.cases }); }}>Review example</Button></TableCell>
          </TableRow>)}</TableBody></Table>
      </TableContainer>}
    </Section>
    {editor && <TestCaseEditor open initialCase={editor.value} canSave={canWrite && !changed} isSaving={save.isPending}
      error={changed ? 'The suite changed. Close and reopen the example after reviewing the latest cases; your edits were not submitted.' : error}
      onClose={() => { if (!save.isPending) setEditor(null); }}
      onSave={async (value) => {
        if (changed) return;
        try {
          await save.mutateAsync({ revision: editor.revision, cases: editor.cases.map((item) => item.id === value.id ? value : item) });
          setEditor(null);
        } catch { setError('Could not save this reviewed example. Your edits are preserved; retry after checking access and the suite revision.'); }
      }} />}
  </Stack>;
}
Tests.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, canWrite: PropTypes.bool.isRequired };
export default function ContextTestsPanel(props) {
  const access = useSkillV11Scope();
  return <Tests key={`${access.identity}:${props.contextId}`} {...props} />;
}
ContextTestsPanel.propTypes = Tests.propTypes;
