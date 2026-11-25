import { useEffect, useRef, useState } from "react";
import {
  fixIsActive,
  useFixRun,
  useFollowFixRun,
} from "src/api/simulate-environments/readinessFix";
import { fixEndCard } from "./readinessView";

/**
 * A fix started from the run plan talks in the builder chat, and keeps running after the plan closes: the chat's turns
 * with the lines it hasn't picked up yet (queued) and, where a fix seen here ended, a card saying how it came out.
 */
export default function useFixInChat(
  envId,
  { enabled, turns, locked, onOpenPlan },
) {
  const fixRun = useFixRun(envId, { enabled }).data ?? null;
  useFollowFixRun(envId, fixRun);
  const activeId = fixIsActive(fixRun) ? fixRun.id : null;
  // The latest fix seen ending here, and the chat turn it ended after. A fix already over when the page loads says
  // nothing.
  const [ended, setEnded] = useState(null);
  const last = useRef(null);
  const lastTurnId = turns?.at(-1)?.id ?? null;
  useEffect(() => {
    if (!fixRun) return;
    const was = last.current;
    last.current = fixRun;
    if (ended && ended.id !== fixRun.id) setEnded(null);
    if (was?.id === fixRun.id && fixIsActive(was) && !fixIsActive(fixRun))
      setEnded({ ...fixRun, afterTurn: lastTurnId });
  }, [fixRun, ended, lastTurnId]);

  const all = [
    ...(turns || []),
    ...(fixRun?.queue || []).map((item, i) => ({
      id: `fix-queued-${i}`,
      role: "user",
      text: item.message,
      queued: true,
    })),
  ];
  // Its outcome is re-read as the fix's draft is saved or discarded.
  const card =
    enabled && ended && fixEndCard(fixRun?.id === ended.id ? fixRun : ended);
  if (card) {
    // Where the fix ended in the chat, so later turns follow the card.
    const at = all.findIndex((turn) => turn.id === ended.afterTurn);
    all.splice(at === -1 ? all.length : at + 1, 0, {
      id: `fix-end-${ended.id}`,
      role: "builder",
      card: { ...card, locked, onAction: onOpenPlan },
    });
  }
  return { turns: all, activeId };
}
