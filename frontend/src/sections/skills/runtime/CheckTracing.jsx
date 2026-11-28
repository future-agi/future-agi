import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { Button, Link, Stack, Tooltip, Typography } from '@mui/material';
import { CHECK_TRACING_LIMIT_MS, runtimeErrorKind, useLatestLoad } from 'src/api/skills/runtime';
import { relativeFrom } from './RuntimeParts';
import {
  CHECK_AGAIN, CHECK_TRACING, CHECK_TRACING_LEAD, CHECK_TRACING_VIEWER, DIAGNOSIS, NO_LOAD_IN_10, RUNTIME_UNAVAILABLE,
  STOP_WAITING, lastFetchReason, received, waitingFor,
} from './runtimeCopy';

const clock = (ms) => {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

/**
 * Check tracing on the Install tab (SKILL-TRACES §7.3, §7.4, UX-14): waits
 * for the next load of this skill (or `context.load` of this context) after
 * the person presses the button, polling endpoint 10 every 5 seconds for at
 * most 10 minutes. Reports the load, or the reason from the last fetch
 * (§6.7), or, after 10 minutes, the diagnosis.
 */
export default function CheckTracing({ kind = 'skill', id, name, canCheck = true, disabledReason = CHECK_TRACING_VIEWER, now: nowProp }) {
  const [startedAt, setStartedAt] = useState(null);
  const [tick, setTick] = useState(() => Date.now());
  const now = nowProp ?? tick;
  const elapsed = startedAt ? Math.max(0, now - startedAt) : 0;
  const timedOut = Boolean(startedAt) && elapsed >= CHECK_TRACING_LIMIT_MS;
  const since = startedAt ? new Date(startedAt).toISOString() : null;
  const query = useLatestLoad({ kind, id, since, active: Boolean(startedAt) && !timedOut });
  const load = query.data?.load ?? null;
  const fetch = query.data?.fetch ?? null;
  const waiting = Boolean(startedAt) && !load && !timedOut && !query.isError;

  useEffect(() => {
    if (!waiting || nowProp !== undefined) return undefined;
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [waiting, nowProp]);

  const start = () => {
    const at = Date.now();
    setTick(at);
    setStartedAt(at);
  };
  const reason = fetch && !fetch.canRecord
    ? lastFetchReason({ sdkVersion: fetch.sdkVersion, language: fetch.client, tracingMode: fetch.tracingMode, minVersion: fetch.minSdkVersion })
    : null;
  const button = (
    <Button variant="outlined" onClick={waiting ? () => setStartedAt(null) : start} disabled={!canCheck || !id} sx={{ width: { xs: 1, sm: 'auto' }, alignSelf: { sm: 'flex-start' } }}>
      {waiting ? STOP_WAITING : startedAt ? CHECK_AGAIN : CHECK_TRACING}
    </Button>
  );

  return (
    <Stack spacing={1} data-check-tracing={waiting ? 'waiting' : load ? 'received' : timedOut ? 'timed-out' : 'idle'}>
      <Typography variant="m3" component="h3">{CHECK_TRACING}</Typography>
      <Typography variant="s3" color="text.secondary">{CHECK_TRACING_LEAD}</Typography>
      {canCheck ? button : <Tooltip title={disabledReason}><span>{button}</span></Tooltip>}
      {!canCheck && <Typography variant="s3" color="text.secondary">{disabledReason}</Typography>}
      <Stack role="status" aria-live="polite" spacing={0.5}>
        {waiting && <Typography variant="s1" color="text.secondary">{waitingFor(clock(elapsed))}</Typography>}
        {load && (
          <Typography variant="s1">
            {received({ name, version: load.version, agent: load.agent?.name || load.agent, ago: relativeFrom(load.receivedAt, now) })}
          </Typography>
        )}
        {!load && reason && <Typography variant="s1" color="text.secondary">{reason}</Typography>}
        {timedOut && !load && (
          <>
            <Typography variant="s1">{NO_LOAD_IN_10}</Typography>
            {!reason && <Typography variant="s3" color="text.secondary">{fetch?.canRecord ? DIAGNOSIS.awaiting : DIAGNOSIS.unknown}</Typography>}
          </>
        )}
        {query.isError && (
          <Typography variant="s3" color="error" role="alert">
            {runtimeErrorKind(query.error) === 'slow' ? DIAGNOSIS.readBudget : RUNTIME_UNAVAILABLE}{' '}
            <Link component="button" type="button" underline="always" color="inherit" onClick={start}>Retry</Link>
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}

CheckTracing.propTypes = {
  kind: PropTypes.oneOf(['skill', 'agent_context']),
  id: PropTypes.string,
  name: PropTypes.string.isRequired,
  canCheck: PropTypes.bool,
  disabledReason: PropTypes.string,
  now: PropTypes.number,
};
