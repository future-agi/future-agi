import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Button, Link, Popover, Stack, Typography } from '@mui/material';
import { paths } from 'src/routes/paths';
import {
  CHANNEL_NAMES, DIAGNOSIS, DIAGNOSIS_ACTIONS, NAME_IT, NAME_IT_LEAD, NAME_IT_SNIPPETS,
  NO_LOADS_RECORDED, TRACE_SKILLS_DOCS, TRACING_FIX, consumerLine,
} from './runtimeCopy';

// First-day and empty states (SKILL-TRACES §7.6, UX-07). "No loads recorded"
// never appears without one of these explanations beside it. The state comes
// from the server's `diagnosis` (§6.6 endpoint 1; the state names are those of
// runtime_usage.runtime_diagnosis), built from fetch accounting (§6.7).

const KIND_WORD = { skill: 'skill', agent_context: 'agent context' };
const joinWords = (words) => (words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`);

function ConsumerList({ consumers, fix = false }) {
  if (!consumers.length) return null;
  return (
    <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2.5 }}>
      {consumers.map((consumer, index) => (
        <Typography component="li" variant="s3" color="text.secondary" key={`${consumer.label}-${consumer.channel}-${index}`}>
          {consumerLine(consumer)}
          {fix && TRACING_FIX[consumer.tracingMode] ? ` — ${TRACING_FIX[consumer.tracingMode]}` : ''}
        </Typography>
      ))}
    </Stack>
  );
}
ConsumerList.propTypes = { consumers: PropTypes.array.isRequired, fix: PropTypes.bool };

/** What the diagnosis says, and its one action, for a state. */
export function diagnosisFor({ diagnosis, kind = 'skill', days = 7, loadsAgents = 0, minSdkVersion }) {
  const word = KIND_WORD[kind] || 'skill';
  const consumers = diagnosis?.consumers ?? [];
  const cannot = consumers.filter((row) => !row.canRecord);
  const sdk = consumers.filter((row) => row.channel === 'sdk' || row.sdkVersion);
  switch (diagnosis?.state) {
    case 'no_tracing_project':
      return { text: DIAGNOSIS.noTracingProject, action: 'setUpTracing' };
    case 'no_readable_projects':
      return { text: DIAGNOSIS.noReadableProjects(word), action: null };
    case 'never_fetched':
      return { text: DIAGNOSIS.neverFetched(word), action: 'install' };
    case 'sdk_cannot_record':
      return { text: DIAGNOSIS.olderSdk({ count: consumers.length, kind: word, days, minVersion: diagnosis.minSdkVersion || minSdkVersion }), action: 'upgrade', list: consumers };
    case 'tracing_unavailable': {
      const off = sdk.filter((row) => row.tracingMode === 'off').length;
      const noOtel = sdk.filter((row) => row.tracingMode === 'no_otel').length;
      return { text: DIAGNOSIS.tracingUnavailable({ count: consumers.length, kind: word, days, off, noOtel }), action: 'fixTracing', list: sdk.filter((row) => ['off', 'no_otel'].includes(row.tracingMode)), fix: true };
    }
    case 'non_sdk_fetches': {
      const channels = [...new Set(consumers.map((row) => CHANNEL_NAMES[row.channel] || row.channel))];
      return { text: DIAGNOSIS.nonSdk({ fetches: diagnosis.fetches, channels: joinWords(channels) || 'the API' }), action: 'docs' };
    }
    case 'awaiting_loads':
      return { text: DIAGNOSIS.awaiting, action: 'checkTracing' };
    case 'partially_recording':
      return { text: DIAGNOSIS.partial({ agents: loadsAgents, others: cannot.length, kind: word }), action: 'seeWhich', list: cannot };
    case 'recording':
      return null;
    default:
      return { text: DIAGNOSIS.unknown, action: 'docs' };
  }
}

/**
 * The diagnosis row for the skill and context Runtime tabs. With `empty`, it
 * leads with "No loads recorded" (the tab has no link rows); otherwise it is
 * the one line above the table (some consumers can't record).
 */
export default function RuntimeDiagnosis({ diagnosis, kind = 'skill', subjectId, days = 7, loadsAgents = 0, empty = false, minSdkVersion, onCheckTracing }) {
  const [open, setOpen] = useState(false);
  const result = diagnosisFor({ diagnosis, kind, days, loadsAgents, minSdkVersion });
  if (!result) return null;
  const detail = subjectId ? (kind === 'agent_context' ? paths.dashboard.skills.contextDetail(subjectId) : paths.dashboard.skills.detail(subjectId)) : null;
  const action = (() => {
    switch (result.action) {
      case 'setUpTracing':
        return <Button size="small" variant="outlined" component={RouterLink} to={paths.dashboard.observe}>{DIAGNOSIS_ACTIONS.setUpTracing}</Button>;
      case 'install':
        return detail ? <Button size="small" variant="outlined" component={RouterLink} to={`${detail}?tab=use`}>{DIAGNOSIS_ACTIONS.install}</Button> : null;
      case 'upgrade':
        return <Link href={`${TRACE_SKILLS_DOCS}#upgrade`} variant="s3">{DIAGNOSIS_ACTIONS.upgrade}</Link>;
      case 'fixTracing':
        return <Link href={`${TRACE_SKILLS_DOCS}#turn-it-on`} variant="s3">{DIAGNOSIS_ACTIONS.fixTracing}</Link>;
      case 'docs':
        return <Link href={TRACE_SKILLS_DOCS} variant="s3">{DIAGNOSIS_ACTIONS.docs}</Link>;
      case 'checkTracing':
        return onCheckTracing
          ? <Button size="small" variant="outlined" onClick={onCheckTracing}>{DIAGNOSIS_ACTIONS.checkTracing}</Button>
          : detail ? <Button size="small" variant="outlined" component={RouterLink} to={`${detail}?tab=use`}>{DIAGNOSIS_ACTIONS.checkTracing}</Button> : null;
      case 'seeWhich':
        return <Button size="small" color="inherit" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{DIAGNOSIS_ACTIONS.seeWhich}</Button>;
      default:
        return null;
    }
  })();
  const listShown = result.list?.length && (result.action !== 'seeWhich' || open);
  return (
    <Stack spacing={1} role="status" data-diagnosis={diagnosis?.state || 'unknown'} sx={{ py: empty ? 2 : 0 }}>
      {empty && <Typography variant="m3" component="h3">{NO_LOADS_RECORDED}</Typography>}
      <Stack direction="row" spacing={1.5} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}>
        <Typography variant="s1" color={empty ? 'text.primary' : 'text.secondary'}>{result.text}</Typography>
        {action}
      </Stack>
      {listShown ? <ConsumerList consumers={result.list} fix={result.fix} /> : null}
    </Stack>
  );
}
RuntimeDiagnosis.propTypes = {
  diagnosis: PropTypes.shape({ state: PropTypes.string, consumers: PropTypes.array, fetches: PropTypes.number, minSdkVersion: PropTypes.string }),
  kind: PropTypes.oneOf(['skill', 'agent_context']),
  subjectId: PropTypes.string,
  days: PropTypes.number,
  loadsAgents: PropTypes.number,
  empty: PropTypes.bool,
  minSdkVersion: PropTypes.string,
  onCheckTracing: PropTypes.func,
};

/**
 * The other §7.6 lines: project cap and unreadable projects, traces without a
 * root, no evals. Each is one quiet line; nothing here names another project.
 */
export function RuntimeNotes({ coverage, truncated = false, incomplete = 0, noEvals = false, onChooseProjects }) {
  const lines = [];
  if (truncated && coverage?.projectsScanned) {
    lines.push({ key: 'cap', text: DIAGNOSIS.projectCap({ shown: coverage.projectsScanned, total: coverage.projectsTotal ?? coverage.projectsScanned }), action: onChooseProjects ? DIAGNOSIS_ACTIONS.chooseProjects : null });
  }
  if (coverage?.unreadableProjects > 0) lines.push({ key: 'unreadable', text: DIAGNOSIS.unreadable(coverage.unreadableProjects) });
  if (incomplete > 0) lines.push({ key: 'root', text: DIAGNOSIS.rootMissing(incomplete) });
  if (noEvals) lines.push({ key: 'evals', text: DIAGNOSIS.noEvals });
  if (!lines.length) return null;
  return (
    <Stack spacing={0.5}>
      {lines.map((line) => (
        <Typography key={line.key} variant="s3" color="text.secondary" data-note={line.key}>
          {line.text}
          {line.action && <>{' '}<Link component="button" type="button" variant="s3" onClick={onChooseProjects}>{line.action}</Link></>}
        </Typography>
      ))}
    </Stack>
  );
}
RuntimeNotes.propTypes = {
  coverage: PropTypes.object,
  truncated: PropTypes.bool,
  incomplete: PropTypes.number,
  noEvals: PropTypes.bool,
  onChooseProjects: PropTypes.func,
};

/** "Name it" for an unnamed agent: the one-line snippet, copyable (§7.6). */
export function NameIt() {
  const [anchor, setAnchor] = useState(null);
  const [copied, setCopied] = useState(null);
  const copy = async (code) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
    } catch {
      setCopied(null);
    }
  };
  return (
    <>
      <Link component="button" type="button" variant="s3" onClick={(event) => { event.stopPropagation(); setAnchor(event.currentTarget); }}>{NAME_IT}</Link>
      <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} onClick={(event) => event.stopPropagation()} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
        <Stack spacing={1.5} sx={{ p: 2, maxWidth: 360 }}>
          <Typography variant="s3">{NAME_IT_LEAD}</Typography>
          {NAME_IT_SNIPPETS.map((snippet) => (
            <Stack key={snippet.label} spacing={0.5}>
              <Typography variant="s3" color="text.secondary">{snippet.label}</Typography>
              <Box component="pre" sx={{ m: 0, p: 1, borderRadius: 1, bgcolor: 'action.hover', typography: 's3', fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>{snippet.code}</Box>
              <Button size="small" color="inherit" onClick={() => copy(snippet.code)} sx={{ alignSelf: 'flex-start' }}>
                {copied === snippet.code ? 'Copied' : 'Copy'}
              </Button>
            </Stack>
          ))}
        </Stack>
      </Popover>
    </>
  );
}
