// Shared hooks of the R4 prototype's live-connection world (implementation; FE-5
// deletes it). One machines query for every pill and strip
// (`useConnectMachines({poll: 5000})`; react-query pauses polling while the
// tab is hidden) and a 1 s clock that only ticks while a timer shows.
// dataset integration FE-0: the connect flow (tokens, sessions, try) is gone.
import { useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { ROLES } from "src/utils/rolePermissionMapping";
import { r4Keys, useConnectMachines } from "src/api/develop/r4";
import { isSurfaceShown } from "src/launch/launchSwitch";

export const MACHINES_POLL_MS = 5000;

/** Every machine the signed-in user may see (`{machines, data, isLoading, …}`). */
export function useMachines(opts = {}) {
  // L6 launch switch (mock:connect-endpoints): connected machines exist only
  // in the prototype world; a launch build registers no query and no poll.
  const on = isSurfaceShown("runners.connect");
  const q = useConnectMachines({
    ...(on ? { poll: MACHINES_POLL_MS } : {}),
    ...opts,
    enabled: on && (opts.enabled ?? true),
  });
  const machines = useMemo(() => q.data?.results ?? [], [q.data]);
  return { ...q, machines };
}

/** One machine by id, or the machine an agent runs on. */
export function useMachine({ machineId, agentId, enabled = true } = {}) {
  const q = useMachines({ enabled });
  const hit = useMemo(() => {
    if (machineId) {
      const m = q.machines.find((x) => x.id === machineId) ?? null;
      const a = agentId ? m?.agents?.find((x) => x.id === agentId) : null;
      return { machine: m, agent: a ?? null };
    }
    if (agentId)
      for (const m of q.machines) {
        const a = (m.agents ?? []).find((x) => x.id === agentId);
        if (a) return { machine: m, agent: a };
      }
    return { machine: null, agent: null };
  }, [q.machines, machineId, agentId]);
  return { ...hit, isLoading: q.isLoading, isError: q.isError };
}

/** Refetch the shared machines query (after a handshake, a PATCH…). */
export function useRefreshMachines() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: r4Keys.machines() });
}

/** `Date.now()` that ticks every `ms` while `active` (timers never drift). */
export function useNow(ms = 1000, active = true) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms, active]);
  return now;
}

/** Viewers read runners but change nothing. */
export function useReadOnly() {
  // Never throws outside AuthProvider (pills and strips render anywhere).
  const role = useContext(AuthContext)?.role;
  return role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
}
