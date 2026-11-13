import PropTypes from 'prop-types';
import { Stack, Typography } from '@mui/material';

// Release selection and plain-language facts share one wrapping row, including on phones.
export default function ContextIdentityStrip({ viewed, latestVersion, productionVersion, draft, extra, releaseControl }) {
  const number = viewed?.version ?? viewed?.number;
  return <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', alignItems: 'center', minWidth: 0 }}>
    {releaseControl || <Typography variant="s3">{viewed ? `Viewing v${number}` : 'No releases yet'}</Typography>}
    <Typography variant="s3" color="text.secondary">
      {latestVersion != null && `${number === latestVersion ? 'Latest' : `Latest v${latestVersion}`} · `}
      {productionVersion == null ? 'Not in production' : `Production v${productionVersion}`}
      {draft && ` · Your draft${draft.baseVersion != null ? ` (from v${draft.baseVersion})` : ''}`}
    </Typography>
    {extra}
  </Stack>;
}
ContextIdentityStrip.propTypes = { viewed: PropTypes.object, latestVersion: PropTypes.number, productionVersion: PropTypes.number, draft: PropTypes.object, extra: PropTypes.node, releaseControl: PropTypes.node };
