import PropTypes from 'prop-types';
import { Stack, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import IdentityChip from '../components/IdentityChip';

// One aligned release control; production and latest are independent roles.
export default function ContextIdentityStrip({ viewed, latestVersion, productionVersion, draft, extra, releaseControl }) {
  return <Stack direction={{ xs: 'column', sm: 'row' }} useFlexGap spacing={1} sx={{ flexWrap: 'wrap', alignItems: { xs: 'flex-start', sm: 'center' }, minWidth: 0 }}>
    {releaseControl || <Typography variant="s3">{viewed ? `Viewing v${viewed.version ?? viewed.number}` : 'No releases yet'}</Typography>}
    <StatusBadge status={productionVersion == null ? 'unset' : 'released'} variant="badge" dot={false} label={productionVersion == null ? 'Not in production' : `Production v${productionVersion}`} />
    {latestVersion != null && <StatusBadge status="unset" variant="badge" dot={false} label={`Latest v${latestVersion}`} />}
    {draft && <Typography variant="s3" color="text.secondary">Your draft{draft.baseVersion != null ? ` (from v${draft.baseVersion})` : ''}</Typography>}
    {extra}
    {viewed?.digest && <IdentityChip value={viewed.digest} label="Digest" inline />}
  </Stack>;
}
ContextIdentityStrip.propTypes = { viewed: PropTypes.object, latestVersion: PropTypes.number, productionVersion: PropTypes.number, draft: PropTypes.object, extra: PropTypes.node, releaseControl: PropTypes.node };
