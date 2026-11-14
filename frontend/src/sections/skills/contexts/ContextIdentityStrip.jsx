import PropTypes from 'prop-types';
import { Stack } from '@mui/material';
import VersionIdentityRow from '../components/VersionIdentityRow';

// Intent: keep the selected bytes, latest publication and production distinct.
// Hierarchy: shared identity row, with the exact-version picker alongside it.
// Palette/depth/surfaces: neutral theme roles and flat page; status alone uses colour.
// Typography/spacing: shared s3 facts, wrapping 8px groups on the 4px grid.
// Once a release exists, the release picker sits in the row's "Viewing" slot (one "Viewing", one
// version); before the first release the plain "No releases yet" note leads the row instead.
export default function ContextIdentityStrip({ viewed, latestVersion, productionVersion, draft, extra, releaseControl }) {
  const published = latestVersion != null || Boolean(viewed);
  return <Stack direction={{ xs: 'column', sm: 'row' }} useFlexGap spacing={1} sx={{ flexWrap: 'wrap', alignItems: { xs: 'flex-start', sm: 'center' }, minWidth: 0 }}>
    {!published && releaseControl}
    <VersionIdentityRow picker={published ? releaseControl : undefined} viewing={{ number: viewed?.version ?? viewed?.number, digest: viewed?.digest }} latest={latestVersion == null ? null : { number: latestVersion }} production={productionVersion == null ? null : { number: productionVersion }} draft={draft ? { baseNumber: draft.baseVersion } : null} extra={extra} />
  </Stack>;
}
ContextIdentityStrip.propTypes = { viewed: PropTypes.object, latestVersion: PropTypes.number, productionVersion: PropTypes.number, draft: PropTypes.object, extra: PropTypes.node, releaseControl: PropTypes.node };
