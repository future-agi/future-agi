import PropTypes from 'prop-types';
import VersionIdentityRow from '../components/VersionIdentityRow';

// Each role stays explicit, even when viewing, latest and production coincide.
export default function ContextIdentityStrip({ viewed, latestVersion, productionVersion, draft, extra }) {
  return <VersionIdentityRow viewing={{ number: viewed?.version ?? viewed?.number, digest: viewed?.digest }}
    latest={latestVersion == null ? null : { number: latestVersion }}
    production={productionVersion == null ? null : { number: productionVersion }}
    draft={draft ? { baseNumber: draft.baseVersion } : null} extra={extra} />;
}
ContextIdentityStrip.propTypes = { viewed: PropTypes.object, latestVersion: PropTypes.number, productionVersion: PropTypes.number, draft: PropTypes.object, extra: PropTypes.node };
