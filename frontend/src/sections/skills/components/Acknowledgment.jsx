import { useCallback, useEffect, useId, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Checkbox, Stack, Typography } from '@mui/material';

// One tick per actor per decision, bound to the identity it was given for
// (a review fingerprint, a preview fingerprint or an ack key) and asked again
// whenever that identity changes, including a change back to an earlier value
// (A → B → A). SKILL-TRACES §15 D16, §16 UXF-02/UXF-03.
//
// The identity must be the server's own binding for the decision (for example
// `reviewFingerprint` or `ackKey`), so a change the page never rendered still
// produces a different value. A missing identity can never be ticked.

const hasIdentity = (identity) => identity !== null && identity !== undefined && identity !== '';

// "Asked once in this session" memory (UXF-02: the final move confirmation asks
// no new tick when the same person ticked the same ack key in this session).
// In memory only: a reload asks again, and nothing reaches web storage.
const sessionTicks = new Map();
const sessionKey = (scope, actorId) => (scope && actorId ? `${scope}\u0000${actorId}` : null);

/** True when `actorId` ticked exactly `identity` for `scope` earlier in this page session. */
// eslint-disable-next-line react-refresh/only-export-components -- the binding helpers ship with their component.
export function acknowledgedInSession({ scope, actorId, identity } = {}) {
  const key = sessionKey(scope, actorId);
  return Boolean(key && hasIdentity(identity) && sessionTicks.get(key) === identity);
}

/** Forget a remembered tick (for example after the decision is sent or fails). */
// eslint-disable-next-line react-refresh/only-export-components -- the binding helpers ship with their component.
export function forgetSessionAcknowledgment({ scope, actorId } = {}) {
  const key = sessionKey(scope, actorId);
  if (key) sessionTicks.delete(key);
}

/** Clears every remembered tick. For tests and sign-out. */
// eslint-disable-next-line react-refresh/only-export-components -- the binding helpers ship with their component.
export function resetSessionAcknowledgments() {
  sessionTicks.clear();
}

/**
 * State for one acknowledgment.
 *
 *   const ack = useAcknowledgment(review.ackKey, { scope: `release:${request.id}`, actorId: me.id });
 *   ack.acknowledged          true only for the current identity
 *   ack.acknowledgedIdentity  the identity the tick was given for (send this, not a later value), else null
 *   ack.changed               the person had ticked, and the identity changed since
 *   ack.setAcknowledged(bool) tick or untick
 *   ack.reset()               untick
 *
 * With `scope` and `actorId`, a tick is recorded for this page session
 * (read it with `acknowledgedInSession`). Only a mount that passes
 * `restore: true` (the final move confirmation, UXF-02) starts ticked from
 * that record, and only for the same scope, actor and exact identity; every
 * other mount asks again. Without an actor nothing is recorded.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the binding helpers ship with their component.
export function useAcknowledgment(identity, { scope, actorId, restore = false } = {}) {
  const valid = hasIdentity(identity);
  const key = sessionKey(scope, actorId);
  const [state, setState] = useState(() => ({
    seen: identity,
    ticked: valid && restore && acknowledgedInSession({ scope, actorId, identity }),
    changed: false,
  }));
  let current = state;
  if (!Object.is(state.seen, identity)) {
    // Derived-state reset during render: no frame ever shows the old tick
    // against the new identity.
    current = { seen: identity, ticked: false, changed: state.ticked || state.changed };
    setState(current);
  }
  const previous = useRef(identity);
  useEffect(() => {
    if (Object.is(previous.current, identity)) return;
    previous.current = identity;
    if (key) sessionTicks.delete(key);
  }, [identity, key]);
  const acknowledged = valid && current.ticked;
  const setAcknowledged = useCallback((checked) => {
    if (!valid) return;
    setState({ seen: identity, ticked: Boolean(checked), changed: false });
    if (!key) return;
    if (checked) sessionTicks.set(key, identity);
    else sessionTicks.delete(key);
  }, [identity, key, valid]);
  const reset = useCallback(() => setAcknowledged(false), [setAcknowledged]);
  return {
    identity,
    acknowledged,
    acknowledgedIdentity: acknowledged ? identity : null,
    changed: !acknowledged && current.changed,
    setAcknowledged,
    reset,
  };
}

const DEFAULT_CHANGED_NOTE = 'This changed after you ticked it. Check it again.';

/**
 * The tick itself. Pass the object from `useAcknowledgment`. The label names
 * the decision ("I've checked the 2 changed files"); once ticked it stays at
 * full contrast even while the form is locked for submitting.
 */
export default function Acknowledgment({ acknowledgment, label, disabled = false, changedNote = DEFAULT_CHANGED_NOTE, id, sx }) {
  const generated = useId();
  const inputId = id || `ack-${generated.replace(/:/g, '')}`;
  const noteId = `${inputId}-changed`;
  const { acknowledged, changed, identity, setAcknowledged } = acknowledgment;
  const unavailable = disabled || !hasIdentity(identity);
  let labelColor = 'text.primary';
  if (!acknowledged && unavailable) labelColor = 'text.disabled';
  return (
    <Stack spacing={0.25} sx={{ minWidth: 0, ...sx }}>
      <Stack direction="row" alignItems="center" sx={{ minWidth: 0, ml: -1 }}>
        <Checkbox
          checked={acknowledged}
          disabled={unavailable}
          onChange={(_, checked) => setAcknowledged(checked)}
          inputProps={{ id: inputId, 'aria-describedby': changed ? noteId : undefined }}
        />
        <Typography component="label" htmlFor={inputId} variant="s3" color={labelColor}
          data-acknowledged={acknowledged ? 'true' : 'false'}
          sx={{ minWidth: 0, overflowWrap: 'anywhere', cursor: unavailable ? 'default' : 'pointer' }}>
          {label}
        </Typography>
      </Stack>
      {changed && <Typography id={noteId} variant="s3" color="text.secondary">{changedNote}</Typography>}
    </Stack>
  );
}

Acknowledgment.propTypes = {
  acknowledgment: PropTypes.shape({
    identity: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    acknowledged: PropTypes.bool.isRequired,
    changed: PropTypes.bool,
    setAcknowledged: PropTypes.func.isRequired,
  }).isRequired,
  label: PropTypes.node.isRequired,
  disabled: PropTypes.bool,
  changedNote: PropTypes.string,
  id: PropTypes.string,
  sx: PropTypes.object,
};
