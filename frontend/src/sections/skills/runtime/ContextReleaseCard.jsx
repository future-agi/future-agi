import PropTypes from 'prop-types';
import { Stack, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import Section from 'src/components/section';
import { runtimeErrorKind, useContextComparison } from 'src/api/skills/runtime';
import { MoveCardBody } from './ProductionMoveCard';
import { RuntimeQueryState } from './RuntimeParts';
import { CANT_OPEN_PROJECT, CHANGED_SKILLS, MOVE_CARD_ERROR, changedSkillLine, contextReleaseTitle } from './runtimeCopy';

/**
 * The context release card (SKILL-TRACES §7.4, §6.6 endpoint 9, UX-11): the
 * move card keyed on the context version, with the skills that changed
 * between the two context versions listed under it. Shown on the context
 * Runtime tab and on the context release receipt.
 */
export default function ContextReleaseCard({ contextId, eventId, projectIds = [], window: moveWindow = '24h', onRollback, variant = 'card' }) {
  const query = useContextComparison(contextId, { eventId, window: moveWindow, projectIds });
  const errorKind = query.isError ? runtimeErrorKind(query.error) : null;
  const body = (() => {
    if (query.isPending && query.fetchStatus !== 'idle') return <ContentSkeleton variant="panel" label="Loading this release" />;
    if (query.isError) return <RuntimeQueryState query={query} errorKind={errorKind} errorTitle={MOVE_CARD_ERROR} deniedText={CANT_OPEN_PROJECT} loadingLabel="Loading this release" compact />;
    if (!query.data) return null;
    const data = query.data;
    const hours = data.collecting?.hoursTotal ?? 24;
    return (
      <MoveCardBody
        data={data}
        subjectKind="agent_context"
        subjectId={contextId}
        title={contextReleaseTitle({ fromVersion: data.fromVersion, toVersion: data.toVersion, windowHours: Math.round(hours) })}
        onRollback={onRollback}
      >
        {data.changedSkills?.length > 0 && (
          <Stack spacing={0.5} data-section="changed-skills">
            <Typography variant="s1" sx={{ fontWeight: 500 }}>{CHANGED_SKILLS}</Typography>
            <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2.5 }}>
              {data.changedSkills.map((item) => <Typography component="li" variant="s3" key={item.skillId}>{changedSkillLine(item)}</Typography>)}
            </Stack>
          </Stack>
        )}
      </MoveCardBody>
    );
  })();
  if (variant === 'plain') return body;
  return <Section variant="panel" aria-label="Context release" data-card="context-release">{body}</Section>;
}

ContextReleaseCard.propTypes = {
  contextId: PropTypes.string.isRequired,
  eventId: PropTypes.string,
  projectIds: PropTypes.array,
  window: PropTypes.string,
  onRollback: PropTypes.func,
  variant: PropTypes.oneOf(['card', 'plain']),
};
