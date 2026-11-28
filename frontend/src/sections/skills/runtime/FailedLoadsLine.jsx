import PropTypes from 'prop-types';
import { Alert, Stack, Typography } from '@mui/material';
import { TraceNumber } from './RuntimeParts';
import { MATCHED_BY_NAME, OPEN_THOSE_TRACES, failedLoadsSentence, mismatchSentence } from './runtimeCopy';

// Links per project: `{all: link}` for one project, or `{<project_id>: {all: link}}`
// when the failures span several projects (§6.6 endpoint 1 `failed_loads.links`).
function projectLinks(links, projects = []) {
  if (!links) return [];
  if (links.all) return [{ projectId: links.all.project_id ?? links.project_id, link: links.all }];
  return Object.entries(links)
    .filter(([, value]) => value?.all)
    .map(([projectId, value]) => {
      const known = projects.find((project) => String(project.projectId) === String(projectId));
      return { projectId, projectName: known?.projectName || known?.name || 'Project', traces: known?.traces, link: value.all };
    });
}

/**
 * "14 loads of refund-policy failed in the last 24 hours (12 integrity, 2
 * authorization)" (§7.3 item 1, UX-04). Failed loads have no version and are
 * matched to the skill by the name the app reported, which the line says.
 * Failed loads are never counted as loads.
 */
export default function FailedLoadsLine({ failedLoads, name, windowValue }) {
  if (!failedLoads?.total) return null;
  const targets = projectLinks(failedLoads.links, failedLoads.projects);
  const sentence = failedLoadsSentence({ total: failedLoads.total, name, windowValue, byErrorType: failedLoads.byErrorType });
  const single = targets.length === 1 ? targets[0] : null;
  return (
    <Alert severity="error" variant="outlined" icon={false} sx={{ py: 0.5 }} data-line="failed-loads">
      <Stack spacing={0.25}>
        <Typography variant="s1">
          {targets.length ? (
            <TraceNumber link={single?.link ?? { ...targets[0].link, project_id: null }} projectId={single?.projectId} projects={single ? undefined : targets} what="Failed loads">
              {sentence}
            </TraceNumber>
          ) : sentence}
        </Typography>
        <Typography variant="s3" color="text.secondary">{MATCHED_BY_NAME}</Typography>
      </Stack>
    </Alert>
  );
}
FailedLoadsLine.propTypes = {
  failedLoads: PropTypes.shape({
    total: PropTypes.number,
    byErrorType: PropTypes.object,
    links: PropTypes.object,
    projects: PropTypes.array,
  }),
  name: PropTypes.string.isRequired,
  windowValue: PropTypes.string,
};

/**
 * "31 loads had contents that don't match the published version. They're
 * left out of the numbers below." (§7.3 item 2, §6.4). A warning line above
 * the table, with a link to those traces.
 */
export function ContentsWarningLine({ mismatch }) {
  if (!mismatch?.loads) return null;
  const link = mismatch.links?.all ?? null;
  return (
    <Alert severity="warning" variant="outlined" icon={false} sx={{ py: 0.5 }} data-line="contents-mismatch">
      <Typography variant="s1">
        {mismatchSentence(mismatch.loads)}{' '}
        {link && <TraceNumber link={link} projects={mismatch.projects} what="Loads with other contents">{OPEN_THOSE_TRACES}</TraceNumber>}
      </Typography>
    </Alert>
  );
}
ContentsWarningLine.propTypes = { mismatch: PropTypes.shape({ loads: PropTypes.number, links: PropTypes.object, projects: PropTypes.array }) };
