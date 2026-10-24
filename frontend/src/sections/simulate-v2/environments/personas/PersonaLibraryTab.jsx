import { useCallback, useMemo, useState } from "react";
import { Box } from "@mui/material";
import PersonaListView from "src/sections/persona/PersonaListView";
import { useSimStore } from "../../store";
import UseInEnvironmentDialog from "./UseInEnvironmentDialog";

/**
 * Environments → Personas. The workspace's persona library — Future AGI
 * built personas plus the ones the team creates — kept outside any one
 * environment so the same caller can be pointed at every agent. Using a
 * persona in an environment writes scenarios for it there.
 */
export default function PersonaLibraryTab() {
  const { state } = useSimStore();
  const [using, setUsing] = useState(null); // personas being used

  const onUse = useCallback((personas) => setUsing(personas), []);

  /* Where each library persona is in use: scenarios carry a copy that
     remembers its library id, so count those across your environments. */
  const usage = useMemo(() => {
    const names = new Map((state.myEnvironments || []).map((e) => [e.id, e.name]));
    const out = {};
    Object.entries(state.byEnv || {}).forEach(([envId, es]) => {
      if (!names.has(envId)) return;
      (es?.scenarios || []).forEach((s) => {
        const id = s.persona?.libraryId;
        if (id == null) return;
        const u = (out[String(id)] ||= { scenarios: 0, envs: [] });
        u.scenarios += 1;
        if (!u.envs.includes(names.get(envId))) u.envs.push(names.get(envId));
      });
    });
    return out;
  }, [state.byEnv, state.myEnvironments]);

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 3, pb: 2 }}>
      <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <PersonaListView framed onUseInEnvironment={onUse} usage={usage} />
      </Box>
      <UseInEnvironmentDialog open={!!using} onClose={() => setUsing(null)} personas={using || []} />
    </Box>
  );
}
