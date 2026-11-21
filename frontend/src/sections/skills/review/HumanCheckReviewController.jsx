import { useState } from 'react';
import PropTypes from 'prop-types';
import { Button, Stack, Typography } from '@mui/material';
import { useSkill } from 'src/api/skills';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useAppendEvidenceAnnotation, useEvidenceAnnotations, useEvidenceReference } from 'src/api/skills/evidence-annotations';
import HumanCheckReview from './HumanCheckReview';

// Intent: append a cited assessment to an exact retained attempt, never regrade it.
// Reuse the flat review section and scoped API boundary; pages describe the run,
// while only annotations for this check are displayed in the form.
function Review({ skillId, runId, attemptId, check }) {
  const access = useSkillV11Scope();
  const [page, setPage] = useState(1);
  const skill = useSkill(skillId);
  const reference = useEvidenceReference('skills_text', runId);
  const annotations = useEvidenceAnnotations('skills_text', runId, page);
  const append = useAppendEvidenceAnnotation('skills_text', runId);
  const state = reference.isError || annotations.isError || skill.isError ? 'error'
    : reference.isLoading || annotations.isLoading || skill.isLoading ? 'loading'
      : reference.data && annotations.data && skill.data ? 'ready' : 'unavailable';
  const refresh = () => { reference.refetch(); annotations.refetch(); skill.refetch(); };
  return <Stack spacing={1}>
    <HumanCheckReview scopeKey={access.identity} state={state}
      reference={reference.data && { source: 'skills_text', sourceId: runId, attemptId, checkId: check.checkId, annotationRevision: reference.data.annotationRevision }}
      machineVerdict={check.verdict} annotations={annotations.data?.results.filter((item) => item.attemptId === attemptId && item.checkId === check.checkId) || []}
      canAnnotate={skill.data?.permissions?.canRunTests === true && state === 'ready'}
      onAppend={(input) => append.mutateAsync(input)} onRefresh={refresh} />
    <Stack direction="row" spacing={1} alignItems="center">
      <Button onClick={refresh}>Reload human review</Button>
      <Button disabled={page <= 1 || annotations.isFetching} onClick={() => setPage((value) => value - 1)}>Previous annotations</Button>
      <Button disabled={!annotations.data || page >= annotations.data.totalPages || annotations.isFetching} onClick={() => setPage((value) => value + 1)}>Next annotations</Button>
    </Stack>
    {annotations.data && <Typography variant="s3">Run annotation page {page} of {Math.max(1, annotations.data.totalPages)}. Only this check&apos;s annotations are shown.</Typography>}
  </Stack>;
}
Review.propTypes = { skillId: PropTypes.string.isRequired, runId: PropTypes.string.isRequired, attemptId: PropTypes.string.isRequired, check: PropTypes.object.isRequired };
export default function HumanCheckReviewController(props) {
  const access = useSkillV11Scope();
  return <Review key={`${access.identity}:${props.runId}:${props.attemptId}:${props.check.checkId}`} {...props} />;
}
HumanCheckReviewController.propTypes = Review.propTypes;
