import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { Button, Stack, Typography } from '@mui/material';
import { useContextDetail } from 'src/api/skills/context-authoring';
import Section from 'src/components/section';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { reviewComposerUrl } from '../utils/review-links';

// The canonical composer resolves authorized people and current evidence in the
// active workspace. No reviewer identity is persisted or carried across logins.
export default function ContextLabelRequestPanel({ contextId, version }) {
  const navigate = useNavigate();
  const detail = useContextDetail(contextId);
  const production = detail.data?.releaseState.labels.find((label) => label.name === 'production')?.version;
  return <Section title="Request release review">
    {detail.isError ? <ErrorState title="Couldn't load the release details." onRetry={detail.refetch} />
      : !detail.data ? <ContentSkeleton variant="panel" />
        : <Stack spacing={1.5}>
          <Typography variant="s1">{production != null ? `Production is v${production}.` : 'Production is not assigned.'} An independent reviewer approves moving it to {version?.number != null ? `v${version.number}` : 'the viewed version'}.</Typography>
          <Stack direction="row"><Button variant="contained" color="primary" disabled={!version?.id || !version?.digest || version?.number == null}
            onClick={() => navigate(reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: detail.data.context.name, candidateId: version?.id, number: version?.number, digest: version?.digest, label: 'production' }))}>Request release review</Button></Stack>
        </Stack>}
  </Section>;
}
ContextLabelRequestPanel.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object };
