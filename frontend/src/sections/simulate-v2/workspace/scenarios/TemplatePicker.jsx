import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid } from "@mui/material";
import { getPacks, getRows } from "../../_mock/scenarios";
import { DIFFICULTY_COLOR } from "../../_mock/environments";
import { SectionCard } from "../../components/primitives";
import { RowSkeleton } from "../../components/loading";
import { ScenarioRow } from "../ScenariosStep";

/**
 * Scenario packs for this environment.
 *
 * Master/detail rather than a flat list: a user picks a pack because of what it
 * *tests*, then wants to see the actual rows before committing. Selecting whole
 * packs is the common case, so that is one click; row-level selection is there
 * when someone wants to trim.
 */
export default function TemplatePicker({ env, onAdd, selected }) {
  const packs = useMemo(() => getPacks(env), [env]);
  const [activePack, setActivePack] = useState(packs[0]?.id);
  const [checked, setChecked] = useState({});
  const [loading, setLoading] = useState(true);

  const rows = useMemo(() => getRows(activePack, env), [activePack, env]);
  const alreadyIn = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);
  const totalRows = useMemo(
    () => packs.reduce((a, p) => a + getRows(p.id, env).length, 0),
    [packs, env],
  );

  useEffect(() => {
    setLoading(true);
    const t = setTimeout(() => setLoading(false), 420);
    return () => clearTimeout(t);
  }, [activePack]);

  const toggle = (id) => setChecked((c) => ({ ...c, [id]: !c[id] }));

  const selectWholePack = (packId) => {
    const packRows = getRows(packId, env);
    setChecked((c) => {
      const next = { ...c };
      packRows.forEach((r) => { next[r.id] = true; });
      return next;
    });
  };

  const chosen = useMemo(
    () => Object.entries(checked).filter(([, v]) => v).map(([k]) => k),
    [checked],
  );

  const collectChosen = () => {
    const all = packs.flatMap((p) => getRows(p.id, env));
    const seen = new Set();
    return all.filter((r) => {
      if (!chosen.includes(r.id) || seen.has(r.id)) return false;
      seen.add(r.id);
      return true;
    });
  };

  return (
    <SectionCard
      title="Scenario packs"
      subtitle={`Built for ${env.name} — ${totalRows} scenarios across ${packs.length} packs`}
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={() => onAdd(collectChosen())}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add {chosen.length || ""} {chosen.length === 1 ? "scenario" : "scenarios"}
        </Button>
      }
    >
      <Grid container spacing={2} sx={{ p: 2.5 }}>
        {/* ── pack list ── */}
        <Grid item xs={12} md={4}>
          <Stack spacing={1.25}>
            {packs.map((p) => {
              const active = activePack === p.id;
              return (
                <Box
                  key={p.id}
                  onClick={() => setActivePack(p.id)}
                  sx={{
                    p: 1.75, borderRadius: 1.25, cursor: "pointer",
                    border: "1px solid",
                    borderColor: active ? "primary.main" : "divider",
                    bgcolor: (t) => active ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05) : "background.paper",
                    transition: "border-color .15s ease",
                    "&:hover": { borderColor: active ? "primary.main" : "text.subtitle" },
                  }}
                >
                  <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1}>
                    <Typography sx={{ typography: "s2", fontWeight: 700 }}>{p.name}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                      {getRows(p.id, env).length}
                    </Typography>
                  </Stack>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
                    {p.blurb}
                  </Typography>
                  <Stack direction="row" alignItems="center" spacing={0.625} sx={{ mt: 1 }}>
                    <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: DIFFICULTY_COLOR[p.difficulty] }} />
                    <Typography sx={{ typography: "s3", color: DIFFICULTY_COLOR[p.difficulty], fontWeight: 600 }}>
                      {p.difficulty}
                    </Typography>
                    <Box flex={1} />
                    <Button
                      size="small"
                      onClick={(e) => { e.stopPropagation(); selectWholePack(p.id); }}
                      sx={{ typography: "s3", minWidth: 0, px: 0.75, color: "primary.main" }}
                    >
                      Select all
                    </Button>
                  </Stack>
                </Box>
              );
            })}
          </Stack>
        </Grid>

        {/* ── rows in the active pack ── */}
        <Grid item xs={12} md={8}>
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
            <Box sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {packs.find((p) => p.id === activePack)?.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {rows.length} scenarios · click to include
              </Typography>
            </Box>
            {loading ? (
              <RowSkeleton rows={5} />
            ) : (
              <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                {rows.map((r, i) => (
                  <ScenarioRow
                    key={r.id}
                    row={r}
                    index={i}
                    selectable
                    checked={!!checked[r.id] || alreadyIn.has(r.id)}
                    onToggle={() => !alreadyIn.has(r.id) && toggle(r.id)}
                  />
                ))}
              </Stack>
            )}
          </Box>
        </Grid>
      </Grid>
    </SectionCard>
  );
}

TemplatePicker.propTypes = {
  env: PropTypes.object.isRequired,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};
