import PropTypes from 'prop-types';
import StatusBadge from 'src/components/status-badge';

const states = {
  added: { status: 'success', label: 'Added' },
  removed: { status: 'failed', label: 'Removed' },
  modified: { status: 'needs_review', label: 'Modified' },
  unchanged: { status: 'inactive', label: 'Unchanged' },
};

export default function FileStatusBadge({ status }) {
  return <StatusBadge {...(states[status] || states.unchanged)} size="sm" />;
}
FileStatusBadge.propTypes = { status: PropTypes.oneOf(['added', 'removed', 'modified', 'unchanged']).isRequired };
