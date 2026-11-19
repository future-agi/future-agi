import PropTypes from 'prop-types';
import { Box, Button, Stack, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import { CHANGE_RULES, releaseRuleSentences } from '../utils/terms';
import './skillIcons';

/**
 * The release rule that applies where a person moves a label, publishes,
 * restores or requests a release (UXF-09). Every sentence is built from the
 * `rules` the caller passes (the workspace's current protected labels and
 * review requirement); nothing is hard-coded and a rule that is not passed is
 * not described. Policy is not changed here.
 *
 * Props:
 *   rules            { label?, protectedLabels?, reviewRequired?, subjectKind? } (see releaseRuleSentences)
 *   canChangeRules   true only for people who may change label protection
 *   onChangeRules    opens the owner's rules dialog; "Change rules" shows only with both set
 */
export default function ReleaseRuleNote({ rules, canChangeRules = false, onChangeRules, id, sx }) {
  const sentences = releaseRuleSentences(rules);
  if (!sentences.length) return null;
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start" id={id} data-testid="release-rule-note" sx={{ minWidth: 0, ...sx }}>
      <Box sx={{ display: 'flex', color: 'text.secondary', pt: '1px', flexShrink: 0 }} aria-hidden>
        <Iconify icon="solar:info-circle-linear" width={16} />
      </Box>
      <Stack direction="row" useFlexGap spacing={0.5} sx={{ flexWrap: 'wrap', alignItems: 'baseline', minWidth: 0 }}>
        <Typography variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>{sentences.join(' ')}</Typography>
        {canChangeRules && onChangeRules && (
          <Button variant="text" size="small" color="inherit" onClick={onChangeRules} sx={{ ml: -0.5 }}>{CHANGE_RULES}</Button>
        )}
      </Stack>
    </Stack>
  );
}

ReleaseRuleNote.propTypes = {
  rules: PropTypes.shape({
    label: PropTypes.string,
    protectedLabels: PropTypes.arrayOf(PropTypes.string),
    reviewRequired: PropTypes.bool,
    subjectKind: PropTypes.oneOf(['skill', 'agent_context']),
  }).isRequired,
  canChangeRules: PropTypes.bool,
  onChangeRules: PropTypes.func,
  id: PropTypes.string,
  sx: PropTypes.object,
};
