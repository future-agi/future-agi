import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import { useSkillComparison } from 'src/api/skills/runtime';
import { runtimeTabUrl } from 'src/sections/skills/utils/traces';
import { moveChipText } from './runtimeCopy';

const HOUR = 3600 * 1000;
const WORSE_KEPT_MS = 7 * 24 * HOUR;

/**
 * Hours elapsed, total and remaining for a move response. Uses the server's
 * collecting figures when present, else `movedAt` and `endsAt`.
 */
export function moveTiming(move, now = Date.now()) {
  const moved = new Date(move?.movedAt).getTime();
  const ends = new Date(move?.endsAt ?? move?.windowEndsAt).getTime();
  const total = move?.collecting?.hoursTotal
    ?? (Number.isFinite(moved) && Number.isFinite(ends) ? (ends - moved) / HOUR : 24);
  const elapsed = move?.collecting?.hoursElapsed
    ?? (Number.isFinite(moved) ? Math.min(Math.max(0, (now - moved) / HOUR), total) : 0);
  const remaining = Number.isFinite(ends) ? Math.max(0, (ends - now) / HOUR) : Math.max(0, total - elapsed);
  return { hoursElapsed: elapsed, hoursTotal: total, hoursRemaining: remaining, endsAt: Number.isFinite(ends) ? ends : null };
}

/**
 * The header move state after a production move (§7.3, UX-01). Shows only
 * `watching`, `worse_so_far`, and `worse` for 7 days after the window
 * closes; `better`, `no_change` and `not_enough_data` show nothing (§15
 * D13). The metric named is the one that triggered the rule. The chip opens
 * the move card.
 */
export function MoveStateChip({ move, skillId, kind = 'skill', now = Date.now() }) {
  if (!move || !['watching', 'worse_so_far', 'worse'].includes(move.verdict)) return null;
  const timing = moveTiming(move, now);
  if (move.verdict === 'worse' && timing.endsAt !== null && now > timing.endsAt + WORSE_KEPT_MS) return null;
  const text = moveChipText({
    verdict: move.verdict,
    toVersion: move.toVersion,
    metric: move.trigger?.metric,
    evalName: move.trigger?.name,
    agents: move.trigger?.agents ?? 0,
    ...timing,
  });
  if (!text) return null;
  const href = runtimeTabUrl({ kind, id: skillId, eventId: move.eventId, window: move.window || '24h' });
  return (
    <Box component={RouterLink} to={href} sx={{ display: 'inline-flex', textDecoration: 'none', color: 'inherit', maxWidth: 1, minWidth: 0 }} aria-label={text}>
      <StatusBadge status={move.verdict} role={move.verdict === 'watching' ? 'neutral' : 'warning'} variant="badge" label={text} />
    </Box>
  );
}
MoveStateChip.propTypes = {
  move: PropTypes.shape({
    eventId: PropTypes.string,
    verdict: PropTypes.string,
    toVersion: PropTypes.number,
    movedAt: PropTypes.string,
    endsAt: PropTypes.string,
    window: PropTypes.string,
    trigger: PropTypes.object,
    collecting: PropTypes.object,
  }),
  skillId: PropTypes.string.isRequired,
  kind: PropTypes.oneOf(['skill', 'agent_context']),
  now: PropTypes.number,
};

/**
 * The skill header chip (UXF5 commit 2 places it beside "Production v2 ·
 * Latest v2"). Hidden while loading, on error and when no project is
 * readable (§7.7).
 */
export default function HeaderMoveChip({ skillId }) {
  const query = useSkillComparison(skillId, { label: 'production' });
  if (!query.data || query.isError) return null;
  return <MoveStateChip move={query.data} skillId={skillId} />;
}
HeaderMoveChip.propTypes = { skillId: PropTypes.string.isRequired };
