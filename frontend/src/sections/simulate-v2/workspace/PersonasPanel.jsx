import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Collapse, Chip, TextField, Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";

/**
 * The cast.
 *
 * Personas aren't a separate library any more — each scenario carries
 * its own, so this tab is a derived view of the scenarios above:
 * every unique persona in play, deduped by its archetype slug, plus
 * the scenarios that use it. Adding a persona means adding a scenario
 * that carries it; there's nothing to inject on this screen.
 */
export default function PersonasPanel({ env, envState, onGo }) {
  const [query, setQuery] = useState("");

  /*
    Dedupe by slug (or the name if the persona predates slugs). For each
    unique persona, keep the full object plus the list of scenarios it
    appears in — that list is what the expanded row shows.
  */
  const personas = useMemo(() => {
    const map = new Map();
    (envState.scenarios || []).forEach((s) => {
      const p = s.persona;
      if (!p) return;
      const key = p.slug || p.name;
      if (!map.has(key)) {
        map.set(key, { persona: p, scenarios: [] });
      }
      map.get(key).scenarios.push(s);
    });
    /* Biggest cast members first — the ones the agent will meet often
       matter more than the one-offs. */
    return [...map.values()].sort((a, b) => b.scenarios.length - a.scenarios.length);
  }, [envState.scenarios]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? personas.filter(({ persona }) => {
        const hay = `${persona.name} ${persona.role || ""} ${(persona.traits || []).join(" ")}`.toLowerCase();
        return hay.includes(q);
      })
    : personas;

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2 }}>
        <Box flex={1}>
          <Stack direction="row" alignItems="baseline" spacing={0.75}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Personas</Typography>
            {personas.length > 0 && (
              <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                ({personas.length})
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 760 }}>
            The cast the agent will meet — one row per unique persona across your scenarios. To
            introduce a new one, add a scenario that carries them.
          </Typography>
        </Box>
      </Stack>

      {personas.length === 0 ? (
        <SectionCard>
          <EmptyState
            icon="solar:users-group-rounded-linear"
            title="No personas yet"
            body="Personas come from scenarios — add a scenario and its persona will appear here."
            action={
              <Button
                variant="contained" color="primary" size="small"
                onClick={() => onGo?.("scenarios")}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                Go to Scenarios
              </Button>
            }
          />
        </SectionCard>
      ) : (
        <SectionCard sx={{ mb: 2 }}>
          <Stack
            direction="row" alignItems="center" spacing={1}
            sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <TextField
              size="small"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search personas by name, role, or trait…"
              InputProps={{
                sx: { typography: "s2" },
                startAdornment: (
                  <Box sx={{ pr: 0.75, pl: 0.25, display: "flex", color: "text.subtitle" }}>
                    <Iconify icon="solar:magnifer-linear" width={14} />
                  </Box>
                ),
              }}
              sx={{ maxWidth: 380, flex: 1 }}
            />
            {q && (
              <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
                {shown.length} of {personas.length}
              </Typography>
            )}
          </Stack>

          {shown.length === 0 ? (
            <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                No personas match your search.
              </Typography>
            </Box>
          ) : (
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {shown.map(({ persona, scenarios: uses }) => (
                <PersonaRow
                  key={persona.slug || persona.name}
                  persona={persona}
                  uses={uses}
                  env={env}
                />
              ))}
            </Stack>
          )}
        </SectionCard>
      )}
    </Box>
  );
}

PersonasPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
};

/* ── one persona ───────────────────────────────────────────────────────────── */

function PersonaRow({ persona, uses }) {
  const [open, setOpen] = useState(false);
  /*
    Requesters carry a role (job title); customers carry an age + voice.
    The meta line uses whichever the persona actually has so the row
    reads correctly for both shapes.
  */
  const isRequester = !!persona.role;
  const meta = isRequester
    ? persona.role
    : [persona.age && `${persona.age}`, persona.voice].filter(Boolean).join(" · ");

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={2}
        onClick={() => setOpen((o) => !o)}
        sx={{
          px: 2.5, py: 1.75, cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />

        {/* archetype icon — requesters are colleagues, customers are single users */}
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center",
            color: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 1 : 0.9),
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.12 : 0.08),
          }}
        >
          <Iconify
            icon={isRequester ? "solar:users-group-two-rounded-linear" : "solar:user-rounded-linear"}
            width={16}
          />
        </Box>

        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {persona.name}
          </Typography>
          {meta && (
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
              {meta}
            </Typography>
          )}
        </Box>

        {/* trait chips — the first two, so the row stays scannable */}
        <Stack
          direction="row" spacing={0.75}
          sx={{ flexShrink: 0, display: { xs: "none", md: "flex" } }}
        >
          {(persona.traits || []).slice(0, 2).map((t) => (
            <Chip
              key={t}
              size="small"
              label={t}
              sx={{
                height: 20, borderRadius: 0.75, color: "text.secondary",
                border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                "& .MuiChip-label": { px: 0.75, typography: "s3" },
              }}
            />
          ))}
          {(persona.traits || []).length > 2 && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", alignSelf: "center" }}>
              +{persona.traits.length - 2}
            </Typography>
          )}
        </Stack>

        {/* usage count — the anchor for the whole row */}
        <Typography
          sx={{
            px: 1, py: 0.25, borderRadius: 0.75,
            typography: "s3", fontWeight: 700, color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.06),
            fontVariantNumeric: "tabular-nums", flexShrink: 0,
          }}
        >
          {uses.length} {uses.length === 1 ? "scenario" : "scenarios"}
        </Typography>
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={2} sx={{ px: 2.5, pb: 2.5, pl: 8 }}>
          {(persona.traits || []).length > 0 && (
            <Box>
              <Label>Traits</Label>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
                {persona.traits.map((t) => (
                  <Chip
                    key={t}
                    size="small"
                    label={t}
                    sx={{
                      height: 20, borderRadius: 0.75, color: "text.secondary",
                      border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                      "& .MuiChip-label": { px: 0.75, typography: "s3" },
                    }}
                  />
                ))}
              </Stack>
            </Box>
          )}

          <Box>
            <Label>Appears in {uses.length} {uses.length === 1 ? "scenario" : "scenarios"}</Label>
            <Stack spacing={0.5}>
              {uses.map((s) => (
                <Stack key={s.id} direction="row" spacing={1.5} alignItems="baseline">
                  <Typography
                    sx={{
                      typography: "s2", fontWeight: 600,
                      fontFamily: "ui-monospace, Menlo, monospace",
                      color: "text.primary", flexShrink: 0,
                    }}
                  >
                    {s.name || s.title}
                  </Typography>
                  {(s.summary || s.useCase) && (
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>
                      {s.summary || s.useCase}
                    </Typography>
                  )}
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </Collapse>
    </Box>
  );
}
PersonaRow.propTypes = {
  persona: PropTypes.object,
  uses: PropTypes.array,
  env: PropTypes.object,
};

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 0.75 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
