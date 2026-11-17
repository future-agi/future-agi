import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { Button, Tooltip } from '@mui/material';
import { useContextDetail } from 'src/api/skills/context-authoring';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { reviewComposerUrl } from '../utils/review-links';
import { requestReleaseFor } from '../utils/terms';

// A row action, not another page heading. The composer still resolves current
// evidence and authorized reviewers; no reviewer identity crosses logins.
export default function ContextLabelRequestPanel({ contextId, version }) {
  const navigate = useNavigate();
  const detail = useContextDetail(contextId);
  const production = detail.data?.releaseState.labels.find((label) => label.name === 'production')?.version;
  if (detail.isError) return <ErrorState title="Couldn't load the release details." onRetry={detail.refetch} />;
  if (!detail.data) return <ContentSkeleton variant="rows" rows={1} />;
  if (production != null && version?.number === production) return null;
  const explanation = `${production != null ? `Production is v${production}.` : 'Production is not assigned.'} An independent reviewer approves moving it to ${version?.number != null ? `v${version.number}` : 'the viewed version'}.`;
  return <Tooltip title={explanation}><span><Button size="small" variant="text" disabled={!version?.id || !version?.digest || version?.number == null}
    onClick={() => navigate(reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: detail.data.context.name, candidateId: version?.id, number: version?.number, digest: version?.digest, label: 'production' }))}>{version?.number != null ? requestReleaseFor(version.number) : 'Request release'}</Button></span></Tooltip>;
}
ContextLabelRequestPanel.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object };
