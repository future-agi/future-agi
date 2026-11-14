import PropTypes from 'prop-types';
import { Link, Stack, Typography } from '@mui/material';
import { planCommand } from './rolloutCommands';
import TechnicalDetails from '../components/TechnicalDetails';

const rolloutDocs = 'https://docs.futureagi.com/docs/skills/campaigns';
const inventoryCommand = 'python -m fi.context inventory --repositories repositories.json';

// A repository owner needs a concrete next step, not an executor-state warning.
// The SDK command plans locally; it never implies a write, upload or delivery.
export default function RolloutInstructions({ inventory = false, version }) {
  return <Stack spacing={1} sx={{ minWidth: 0, width: 1 }}>
    <Typography component="pre" variant="s1" sx={{ m: 0, p: 1.5, bgcolor: 'background.default', border: 1, borderColor: 'divider', borderRadius: 1, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: 1, userSelect: 'all' }}>{inventory ? inventoryCommand : planCommand(version)}</Typography>
    <Typography variant="s3" color="text.secondary">{inventory ? 'List repository_id and project paths in repositories.json. This command reads the selected repositories without changing files.' : 'Run in the repository folder with a scoped context credential. Set CONTEXT_NAME to the context name; use your client profile. Submit the generated plan with the SDK’s record_plan, then review it here.'}{!inventory && !Number.isInteger(version) && ' Set VERSION to the required version for this repository.'}</Typography>
    <Link href={rolloutDocs} target="_blank" rel="noopener noreferrer" variant="s1">{inventory ? 'Prepare a repository inventory' : 'Plan and submit repository changes'}</Link>
    <TechnicalDetails title="Rollout requirements"><Typography variant="s3">Native client profiles are preview; allowing preview is not proof of delivery. Repository rollouts require the deployment setting SKILLS_CONTEXT_CAMPAIGNS_ENABLED and a qualified local executor. A local plan does not authorize writes. Approval, pull requests, merges, installation and app delivery are separate steps. Deliberate version pins stay unchanged.</Typography></TechnicalDetails>
  </Stack>;
}
RolloutInstructions.propTypes = { inventory: PropTypes.bool, version: PropTypes.number };
