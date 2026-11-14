import PropTypes from 'prop-types';
import { Box, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import IdentityChip from './IdentityChip';

// The signature row (UX-R7 §2.3): one wrapping line with an 8px gap.
// "Viewing [picker]" comes first when a picker is supplied; the picker carries
// its own accessible name, so no second visible label is rendered.
export default function VersionIdentityRow({ viewing, latest, production, draft, extra, picker }) {
  return <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1, minWidth: 0, maxWidth: 1 }}>
    <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1, minWidth: 0, maxWidth: 1 }}>
      {picker
        ? <><Typography component="span" variant="s3" color="text.secondary">Viewing</Typography><Box sx={{ minWidth: 0 }}>{picker}</Box></>
        : <Typography variant="s3" color="text.secondary">Viewing <Box component="span" sx={{ fontWeight: 600, color: 'text.primary' }}>{viewing?.number != null ? `v${viewing.number}` : 'Not reported'}</Box></Typography>}
      {viewing?.digest && <IdentityChip value={viewing.digest} label="Digest" inline />}
    </Box>
    <StatusBadge status={production?.number != null ? 'released' : 'unset'} label={production?.number != null ? `Production v${production.number}` : 'No production'} />
    {/* Keep role names distinct even when all three refer to the same version. */}
    {latest?.number != null && <Typography variant="s3" color="text.secondary">Latest v{latest.number}</Typography>}
    {draft && <Typography variant="s3" color="text.secondary">Your draft{draft.baseNumber != null ? ` (from v${draft.baseNumber})` : ' (base not reported)'}</Typography>}
    {extra && <Box sx={{ minWidth: 0, maxWidth: 1 }}>{extra}</Box>}
  </Box>;
}

const version = PropTypes.shape({ number: PropTypes.number, digest: PropTypes.string });
VersionIdentityRow.propTypes = {
  viewing: version.isRequired,
  latest: version,
  production: version,
  draft: PropTypes.shape({ baseNumber: PropTypes.number }),
  extra: PropTypes.node,
  picker: PropTypes.node,
};
