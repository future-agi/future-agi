import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Typography } from '@mui/material';
import { saveAs } from 'file-saver';
import { useMigrationExport } from 'src/api/skills/j1-migration-export';
import { useSkillV11Scope } from 'src/api/skills/testing';
import Section from 'src/components/section';

function ExportVersion({ resourceId, number }) {
  const request = useMigrationExport(resourceId, number);
  const [files, setFiles] = useState(null);
  const [error, setError] = useState('');
  const prepare = async () => {
    setError('');
    setFiles(null);
    try { setFiles(await request.mutateAsync()); }
    catch { setError('Could not prepare this exact export. Check your access and try again.'); }
  };
  return <Section title="Export with folder structure">
    <Stack spacing={1.5}>
      <Typography variant="s1">Prepare v{number}’s files and a mapping file to download, so you can re-import later.</Typography>
      <Typography variant="s3" color="text.secondary">Keep both downloads together. This does not change the source.</Typography>
      <Button variant="contained" color="primary" sx={{ alignSelf: 'flex-end' }} disabled={request.isPending} onClick={prepare}>{request.isPending ? 'Preparing export…' : `Prepare v${number} migration export`}</Button>
      {error && <Alert severity="error">{error}</Alert>}
      {files && <>
        <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>v{number} · {files.versionId} · {files.digest} · {files.mappingCount} source mappings</Typography>
        {!files.mappingCount && <Alert severity="info">This version has no retained migration mapping. Its bytes can be exported, but no external source identity is inferred.</Alert>}
        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
          <Button variant="outlined" onClick={() => saveAs(files.archive, `skill-${resourceId}-v${number}.zip`)}>Download exact ZIP</Button>
          <Button variant="outlined" onClick={() => saveAs(files.receipt, `skill-${resourceId}-v${number}-mapping.json`)}>Download mapping receipt</Button>
        </Stack>
      </>}
    </Stack>
  </Section>;
}
ExportVersion.propTypes = { resourceId: PropTypes.string.isRequired, number: PropTypes.number.isRequired };

export default function J1MigrationExport(props) {
  const { identity } = useSkillV11Scope();
  if (!Number.isSafeInteger(props.number) || props.number < 1) return null;
  return <ExportVersion key={`${identity}:${props.resourceId}:${props.number}`} {...props} />;
}
J1MigrationExport.propTypes = { resourceId: PropTypes.string.isRequired, number: PropTypes.number };
