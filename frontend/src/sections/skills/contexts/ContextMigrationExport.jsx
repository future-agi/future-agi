import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Typography } from '@mui/material';
import { saveAs } from 'file-saver';
import { useMigrationCollectionExport } from 'src/api/skills/j1-migration-export';
import Section from 'src/components/section';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';

// Intent: leave a pilot with one download of every linked skill's current
// supported bytes and source mapping, instead of one export per skill.
// Hierarchy: what is exported, one action, then the exact exported identities.
// Palette/depth/surfaces: section on the page, neutral text, error only on failure.
// Typography/spacing: s1/s3 and 12px groups.
export default function ContextMigrationExport({ contextName, links }) {
  const request = useMigrationCollectionExport();
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const skillIds = [...new Set(links.map((link) => link.skillId).filter(Boolean))];
  const run = async () => {
    setError('');
    setItems(null);
    try {
      const result = await request.mutateAsync({ skillIds });
      saveAs(result.archive, `${contextName}-migration-export.zip`);
      setItems(result.items);
    } catch (failure) { setError(failure?.message || 'Could not prepare this export. Nothing was downloaded.'); }
  };
  // Grouped under a foldable heading below the install command, but shown open: the export button is
  // a frozen, measured step (UX-R7 §4.3, §4.6), so reaching it never costs an extra gesture.
  return <TechnicalDetails title="More ways to install" defaultOpen><Section title="Download newer skill versions (not what this context locks)">
    <Stack spacing={1.5}>
      <Typography variant="s3" color="text.secondary">These are the newest published versions, which may differ from the versions this context installs. One ZIP holds each of the {skillIds.length} linked skills with its source mapping receipt. Downloading does not modify the source, remove local files or revoke keys.</Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} useFlexGap>
        <Button variant="outlined" disabled={!skillIds.length || request.isPending} onClick={run}>{request.isPending ? 'Preparing export…' : `Download ${skillIds.length} linked skills`}</Button>
      </Stack>
      {error && <Alert severity="error">{error}</Alert>}
      {items && <Stack spacing={0.5}>
        {items.map((item) => <Stack key={item.resource_id} spacing={0.5}><Typography variant="s3">{item.name} · v{item.version} · {item.mapping_receipts} source mappings</Typography><IdentityChip value={item.digest} label="Export digest" /></Stack>)}
      </Stack>}
    </Stack>
  </Section></TechnicalDetails>;
}
ContextMigrationExport.propTypes = { contextName: PropTypes.string.isRequired, links: PropTypes.array.isRequired };
