import PropTypes from 'prop-types';
import { Link, Stack, Typography } from '@mui/material';
import { planCommand } from './rolloutCommands';
import { ROLLOUT_DOCS_URL } from './rolloutCopy';
import TechnicalDetails from '../components/TechnicalDetails';
import CodeBlock from 'src/components/code-block';

const inventoryCommand = 'python -m fi.context inventory --repositories repositories.json';

// A repository owner needs a concrete next step, not an executor-state warning.
// The SDK command plans locally; it never implies a write, upload or delivery.
export default function RolloutInstructions({ inventory = false, version }) {
  return <Stack spacing={1} sx={{ minWidth: 0, width: 1 }}>
    <CodeBlock wrap code={inventory ? inventoryCommand : planCommand(version)} label={inventory ? 'Inventory command' : 'Plan command'} copyLabel="Copy" ariaLabel={inventory ? 'Copy inventory command' : 'Copy command'} />
    <Typography variant="s3" color="text.secondary">{inventory ? 'This reads the repositories you list and changes no files.' : 'Run this in the repository folder. It plans the change on your machine and writes nothing. Each repository records its plan with a read-only key, then you review it here. Set CONTEXT_NAME to the context name first.'}{!inventory && !Number.isInteger(version) && ' Set VERSION to the release this repository needs.'}</Typography>
    <Link href={ROLLOUT_DOCS_URL} target="_blank" rel="noopener noreferrer" variant="s1">{inventory ? 'Read the inventory guide' : 'Plan and submit repository changes'}</Link>
    <TechnicalDetails title="Command details"><Stack spacing={1}>
      {inventory
        ? <Typography variant="s3">List each repository&apos;s repository_id and project path in repositories.json.</Typography>
        : <Typography variant="s3">Submit the generated plan with the SDK&apos;s record_plan call, using a scoped context credential that can read but not write.</Typography>}
      <Typography variant="s3">Native client profiles are preview; allowing preview is not proof of delivery. Repository rollouts require the deployment setting SKILLS_CONTEXT_CAMPAIGNS_ENABLED and a qualified local executor. A local plan does not authorize writes. Approval, pull requests, merges, installation and app delivery are separate steps. Deliberate version pins stay unchanged.</Typography>
    </Stack></TechnicalDetails>
  </Stack>;
}
RolloutInstructions.propTypes = { inventory: PropTypes.bool, version: PropTypes.number };
