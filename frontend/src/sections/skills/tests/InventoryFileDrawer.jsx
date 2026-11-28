import PropTypes from 'prop-types';
import { Box, Button, Drawer, IconButton, Stack, Typography } from '@mui/material';
import { readContextText } from 'src/api/skills/context-production';
import { skillV11Result, useSkillV11Query } from 'src/api/skills/testing';
import { readSkillFile } from 'src/api/skills';
import { endpoints } from 'src/api/skills/endpoints';
import axios from 'src/utils/axios';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import Iconify from 'src/components/iconify';
import IdentityChip from '../components/IdentityChip';

import { verifyInventoryText } from './verifyInventoryText';
import './testIcons';

export default function InventoryFileDrawer({ subjectId, subjectKind, candidate, bundle, file, onClose }) {
  const supported = Boolean(candidate && bundle.skillId && (candidate.version || candidate.draftRevision || candidate.draft_revision));
  const query = useSkillV11Query({
    parts: ['test-inventory-file', subjectKind, subjectId, candidate, bundle.digest, file.path, file.sha256],
    enabled: Boolean(supported),
    load: async (config) => {
      const revision = candidate.draftRevision ?? candidate.draft_revision;
      let text;
      if (subjectKind === 'agent_context') {
        if (bundle.skillId !== subjectId) throw new Error('The inventory source is unavailable.');
        const result = await readContextText({ contextId: subjectId, number: candidate.version, revision, path: file.path, expectedDigest: bundle.digest, expectedSha256: file.sha256, config });
        text = result.text;
      } else if (revision != null) {
        const draft = skillV11Result(await axios.get(endpoints.draft(bundle.skillId), config));
        if (draft.revision !== revision) throw new Error('The saved draft changed. Preview again.');
        text = draft.files.find((item) => item.path === file.path)?.content;
      } else {
        const result = await readSkillFile(bundle.skillId, candidate.version, file.path, config.signal, config.headers);
        text = result.text;
      }
      if (typeof text !== 'string') throw new Error('The complete file is unavailable.');
      return verifyInventoryText(text, file.sha256);
    },
  });
  return <Drawer open anchor="right" onClose={onClose} aria-labelledby="inventory-file-title" PaperProps={{ sx: { width: { xs: '100%', sm: 560 }, maxWidth: '100vw', p: 3 } }}>
    <Stack spacing={1.5}>
      <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography id="inventory-file-title" component="h2" variant="m1" sx={{ overflowWrap: 'anywhere' }}>{file.path}</Typography><IconButton aria-label="Close" onClick={onClose}><Iconify icon="solar:close-linear" /></IconButton></Stack>
      {typeof query.data !== 'string' && <IdentityChip value={file.sha256} label="Digest" />}
      {!supported ? <Typography variant="s1">This file&apos;s content isn&apos;t available for inspection.</Typography> : query.isLoading ? <ContentSkeleton variant="panel" label="Loading the attested file" /> : query.isError ? <ErrorState title="This file's content isn't available for inspection." detail="The complete file must match the inventory digest before it can be shown." onRetry={query.refetch} /> : typeof query.data === 'string' && <><Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center"><Typography variant="s3" color="text.secondary">Matches the reviewed file.</Typography><IdentityChip value={file.sha256} label="Digest" /></Stack><Box component="pre" tabIndex={0} sx={{ m: 0, typography: 's1', fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{query.data}</Box></>}
      <Box sx={{ pt: 1.5, borderTop: 1, borderColor: 'divider' }}><Button variant="outlined" onClick={onClose}>Close file</Button></Box>
    </Stack>
  </Drawer>;
}
InventoryFileDrawer.propTypes = { subjectId: PropTypes.string, subjectKind: PropTypes.string, candidate: PropTypes.object, bundle: PropTypes.object.isRequired, file: PropTypes.object.isRequired, onClose: PropTypes.func.isRequired };
