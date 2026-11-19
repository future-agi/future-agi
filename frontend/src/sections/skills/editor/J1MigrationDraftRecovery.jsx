import PropTypes from 'prop-types';
import { Stack } from '@mui/material';
import { useCheckpointDraft } from 'src/api/skills/r3-intake';
import { useSkillV11Scope } from 'src/api/skills/testing';
import PageHeader from 'src/components/page-header';
import RecoverableDraftChanges from './RecoverableDraftChanges';

// Retained migration must preserve the author's actual saved revision before
// replacement. Reuse the same recovery operation/key and newer-edit guard as
// context authoring; never turn the migration's Preserve action into deletion.
export default function J1MigrationDraftRecovery({ skill, session, onPreserved }) {
  const { identity } = useSkillV11Scope();
  const checkpoint = useCheckpointDraft('skill', skill.id);
  const scopeKey = `${identity}:skill:${skill.id}:migration`;
  return <Stack spacing={2} sx={{ minWidth: 0, overflowY: 'auto' }}>
    <PageHeader title={skill.name} description="Preserve your draft, then preview the retained source again. Published content and labels stay unchanged." />
    <RecoverableDraftChanges key={scopeKey} scopeKey={scopeKey} session={session}
      onSuspend={(input, options) => checkpoint.mutateAsync({ ...input, ...options, suspend: true })}
      onPreserved={onPreserved} />
  </Stack>;
}
J1MigrationDraftRecovery.propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired }).isRequired,
  session: PropTypes.object.isRequired,
  onPreserved: PropTypes.func.isRequired,
};
