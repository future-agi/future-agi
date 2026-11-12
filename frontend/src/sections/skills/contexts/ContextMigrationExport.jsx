import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Stack, Typography } from '@mui/material';
import { saveAs } from 'file-saver';
import { useMigrationCollectionExport } from 'src/api/skills/j1-migration-export';
import Section from 'src/components/section';

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
  return <Section title="Export linked skills">
    <Stack spacing={1.5}>
      <Typography variant="s1">Downloads the current published version of each of the {skillIds.length} linked skills as its exact archive with its source mapping receipt, in one ZIP. Export does not modify the source, remove local files or revoke credentials.</Typography>
      <Box><Button variant="outlined" disabled={!skillIds.length || request.isPending} onClick={run}>{request.isPending ? 'Preparing export…' : `Export ${skillIds.length} linked skills with mapping receipts`}</Button></Box>
      {error && <Alert severity="error">{error}</Alert>}
      {items && <Stack spacing={0.5}>
        {items.map((item) => <Typography key={item.resource_id} variant="s3" sx={{ overflowWrap: 'anywhere' }}>{item.name} · v{item.version} · {item.digest} · {item.mapping_receipts} source mappings</Typography>)}
      </Stack>}
    </Stack>
  </Section>;
}
ContextMigrationExport.propTypes = { contextName: PropTypes.string.isRequired, links: PropTypes.array.isRequired };
