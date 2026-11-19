import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Link, Typography } from '@mui/material';
import { useContextComparison, useSkillComparison } from 'src/api/skills/runtime';
import { runtimeTabUrl } from 'src/sections/skills/utils/traces';
import { moveTiming } from './MoveStateChip';
import { VERDICT_TEXT, hoursLeft, receiptLoads } from './runtimeCopy';

/** Agents seen on the new version after the move (adoption rows with loads on it). */
export function agentsSinceMove(data) {
  if (Number.isFinite(data?.afterAgents)) return data.afterAgents;
  return (data?.adoption || []).filter((row) => row.notMovedReason === 'got_new' || (row.share ?? 0) > 0).length;
}

function verdictPhrase(data) {
  if (data.verdict === 'watching') return `${VERDICT_TEXT.watching}, ${hoursLeft(moveTiming(data).hoursRemaining)}`;
  return VERDICT_TEXT[data.verdict] || null;
}

/**
 * The receipt's second line (SKILL-TRACES §7.3 "Links into the card", UX-01):
 * the receipt keeps "Check apps picked up v2" for delivery and adds what loads
 * report, "Loaded by 4 agents since the move · Watching, 18 h left", linking
 * to the move card. Quiet while loading and on error: the receipt is the
 * record, this line is a pointer.
 */
export default function ReceiptLoadsLine({ kind = 'skill', subjectId, eventId }) {
  const context = kind === 'agent_context';
  const skill = useSkillComparison(subjectId, { eventId, enabled: !context && Boolean(eventId) });
  const release = useContextComparison(subjectId, { eventId, enabled: context && Boolean(eventId) });
  const query = context ? release : skill;
  if (!eventId || !query.data || query.isError) return null;
  const href = runtimeTabUrl({ kind, id: subjectId, eventId, window: '24h' });
  return (
    <Typography variant="s3" color="text.secondary" data-line="receipt-loads">
      <Link component={RouterLink} to={href} underline="hover" color="inherit">
        {receiptLoads({ agents: agentsSinceMove(query.data), verdictText: verdictPhrase(query.data) })}
      </Link>
    </Typography>
  );
}

ReceiptLoadsLine.propTypes = {
  kind: PropTypes.oneOf(['skill', 'agent_context']),
  subjectId: PropTypes.string.isRequired,
  eventId: PropTypes.string,
};
