import PropTypes from 'prop-types';
import { Link } from '@mui/material';
import CapabilityNotice from '../components/CapabilityNotice';
import { ROLLOUT_DOCS_URL, ROLLOUTS_OFF_ADMIN, ROLLOUTS_OFF_MEMBER } from './rolloutCopy';

/**
 * The one "Repository rollouts are off" notice. Rollouts are a deployment setting, so an admin is
 * pointed at the deployment configuration and a member at an admin. Until the role is known, neither
 * sentence shows: a wrong instruction is worse than none. Presentation only; the server decides.
 */
export default function RolloutsOffNotice({ access = { ready: false, canAdmin: false }, lead = "New repository changes can't be started. Existing rollout records stay readable." }) {
  const role = !access.ready ? '' : access.canAdmin ? ROLLOUTS_OFF_ADMIN : ROLLOUTS_OFF_MEMBER;
  return <CapabilityNotice title="Repository rollouts are off" description={[lead, role].filter(Boolean).join(' ')}
    actions={access.ready && access.canAdmin ? <Link href={ROLLOUT_DOCS_URL} target="_blank" rel="noopener noreferrer" variant="s1">Read the rollout setup guide</Link> : undefined}
    details="SKILLS_CONTEXT_CAMPAIGNS_ENABLED is a deployment environment setting, not a workspace toggle. No repository writes can be authorized or claimed while it is off, and a qualified local executor is still required." />;
}
RolloutsOffNotice.propTypes = { access: PropTypes.shape({ ready: PropTypes.bool, canAdmin: PropTypes.bool }), lead: PropTypes.string };
