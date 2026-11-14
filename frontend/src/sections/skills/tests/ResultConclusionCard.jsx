import PropTypes from 'prop-types';
import { Stack, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import TechnicalDetails from '../components/TechnicalDetails';

// One conclusion card for every Tests result (answer checks and application verification), after
// Vel's fix-run-done screen: the one-sentence conclusion first, then its badge, then the next step
// with its hint, then "How it was decided" as a disclosure. Evidence rows sit below the card.
// The card states what the server reported; it never infers a verdict itself.
export default function ResultConclusionCard({ sentence, sentenceProps, meta, badge, actions, hint, decided, children }) {
  return <Stack spacing={1} sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1, minWidth: 0 }}>
    <Typography variant="m3" component="p" sx={{ m: 0 }} {...sentenceProps}>{sentence}</Typography>
    {meta}
    {children}
    {badge && <StatusBadge status={badge.status} role={badge.role} label={badge.label} sx={{ alignSelf: 'flex-start' }} />}
    {(actions || hint) && <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }}>
      {actions}
      {hint}
    </Stack>}
    {decided && <TechnicalDetails title="How it was decided">{decided}</TechnicalDetails>}
  </Stack>;
}
ResultConclusionCard.propTypes = {
  /** The conclusion, in one sentence. */
  sentence: PropTypes.node.isRequired,
  /** Extra props for the sentence (e.g. role="status" while a run is live). */
  sentenceProps: PropTypes.object,
  /** A quiet s3 line under the sentence: run state and when it started. */
  meta: PropTypes.node,
  /** { status, label, role } for the one readiness badge. */
  badge: PropTypes.shape({ status: PropTypes.string.isRequired, label: PropTypes.string, role: PropTypes.string }),
  /** The next step: at most one contained button, others outlined. */
  actions: PropTypes.node,
  /** One s3 sentence beside the actions. */
  hint: PropTypes.node,
  /** Contents of the "How it was decided" disclosure. */
  decided: PropTypes.node,
  /** Rows between the sentence and the badge (e.g. the human-review line). */
  children: PropTypes.node,
};
