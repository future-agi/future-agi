import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { episodeContract } from "src/api/simulate-environments/_fixtures/rlContract";
import SectionCard from "../../components/SectionCard";
// Actors section (ActorsPanel) is commented out below — it was a dummy cast from
// the fixture ACTOR_LIBRARY, not backed by §6. To be picked up later.
// import ActorsPanel from "./ActorsPanel";
import CapabilityGraph from "../overview/CapabilityGraph";
import WorldInternalsSection from "./WorldInternalsSection";
import { CONTRACT_COPY } from "./contract.constants";

// Map the real §6 `end_conditions` onto the episode-card shape. Returns null when
// the environment carries no real end conditions, so the caller falls back to the
// fixture `episodeContract`. `ended_reasons` is the vocabulary a finished call may
// carry (terminate); the two limits are the run's truncation ceilings.
const CLOCK_NOTE = {
  "real-time": "Wall-clock time: the voice call runs in real time.",
  stepped: "Stepped: the chat advances a turn at a time.",
};
function realEpisode(env) {
  const ec = env?.endConditions;
  if (!ec) return null;
  const truncate = [
    ec.max_turns != null && {
      when: `${ec.max_turns} turns`,
      note: "Ceiling across the suite. Each scenario carries its own.",
    },
    ec.max_duration_seconds != null && {
      when: `${ec.max_duration_seconds}s`,
      note: "The job's wall-clock limit.",
    },
  ].filter(Boolean);
  return {
    terminate: (ec.ended_reasons || []).map((reason) => ({ when: reason, note: "" })),
    truncate,
    clock: {
      mode: ec.clock || "-",
      note: CLOCK_NOTE[ec.clock] || "How run time is measured.",
    },
    // No §6 field for the deterministic seed note; keep the fixture's copy.
    seed: episodeContract(env).seed,
  };
}

/**
 * The environment contract — what this environment IS.
 *
 * Concrete over formal: the world (capability graph), the internals a run reads
 * from (DB schema, tool code, check code), how a run ends, and who else acts in
 * it. The five-part RL-textbook framing that used to live here — modality
 * adapter, obs/action spaces, transition dynamics, reward spec — mostly
 * duplicated other tabs in RL notation, so it was removed. The RL vocabulary
 * still lives in `_fixtures/rlContract.js` if it needs to come back.
 *
 * Twin-backed envs are out of scope in this integration, so the world is always
 * the capability graph (the designer's TwinSandboxSection branch is omitted).
 */
export default function RlContractPanel({ env, envState, patch, locked = false, graphData }) {
  // Prefer the real §6 `end_conditions` when authored, mapped into the same
  // terminate/truncate/clock/seed shape the card renders: `ended_reasons` are the
  // ways a run terminates, `max_turns`/`max_duration_seconds` are the truncation
  // ceilings, `clock` is real-time (voice) or stepped (chat). Falls back to the
  // fixture episode while §6 has not authored it (or the detail path is off).
  const episode = realEpisode(env) || episodeContract(env);
  const hasWorld = !!envState?.agent || (env.tools?.length || 0) > 0;

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
          {CONTRACT_COPY.heading}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
          What this environment is made of: the world runs execute against, the code
          behind every tool call and grader, and how each run ends.
        </Typography>
      </Box>

      {/* ── the world ─────────────────────────────────────────────────── */}
      {hasWorld && (
        <Box sx={{ mb: 2 }}>
          <CapabilityGraph env={env} envState={envState} data={graphData} />
        </Box>
      )}

      {/* ── internals: DB schema + tool code + check code ─────────────── */}
      <WorldInternalsSection env={env} envState={envState} patch={patch} locked={locked} />

      {/* ── run end conditions ────────────────────────────────────────── */}
      <SectionCard
        title="Run end conditions"
        subtitle="When a run is over, and which kind of over it was"
        sx={{ mb: 2 }}
      >
        <Box
          sx={{
            display: "grid", gap: "1px",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.07),
          }}
        >
          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>Terminate</Label>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
              The episode has a value. Goal verifiers settle.
            </Typography>
            <Stack spacing={1}>
              {episode.terminate.map((e) => (
                <Box key={e.when}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{e.when}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{e.note}</Typography>
                </Box>
              ))}
            </Stack>
          </Box>
          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>Truncate</Label>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
              Not a failure, and not terminal. Bootstrap from the last state.
            </Typography>
            <Stack spacing={1}>
              {episode.truncate.map((e) => (
                <Box key={e.when}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>{e.when}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{e.note}</Typography>
                </Box>
              ))}
            </Stack>
          </Box>
        </Box>
        <Stack
          divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
          sx={{ borderTop: "1px solid", borderColor: "divider" }}
        >
          <Stack direction="row" spacing={1.75} sx={{ px: 2.5, py: 1.75 }} alignItems="flex-start">
            <Iconify icon="solar:clock-circle-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>Clock · {episode.clock.mode}</Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>{episode.clock.note}</Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1.75} sx={{ px: 2.5, py: 1.75 }} alignItems="flex-start">
            <Iconify icon="solar:dice-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>Deterministic seed</Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>{episode.seed.note}</Typography>
            </Box>
          </Stack>
        </Stack>
      </SectionCard>

      {/*
        Actors section removed — it was a dummy cast from the fixture
        ACTOR_LIBRARY (not backed by §6), and actors were also dropped from the
        capability graph (now Personas only). To be picked up later if a real
        actors field lands.
        <ActorsPanel env={env} envState={envState} patch={patch} onGo={onGo} locked={locked} />
      */}
    </Box>
  );
}

const ENV_SHAPE = PropTypes.shape({
  surface: PropTypes.string,
  tools: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string, desc: PropTypes.string })),
  rules: PropTypes.arrayOf(PropTypes.string),
  seed: PropTypes.shape({ tables: PropTypes.arrayOf(PropTypes.shape({ name: PropTypes.string })) }),
});

const ENV_STATE_SHAPE = PropTypes.shape({
  agent: PropTypes.shape({ name: PropTypes.string }),
  evals: PropTypes.arrayOf(PropTypes.oneOfType([PropTypes.string, PropTypes.shape({ id: PropTypes.string })])),
  actors: PropTypes.arrayOf(PropTypes.string),
  toolResolutions: PropTypes.objectOf(PropTypes.string),
});

RlContractPanel.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  patch: PropTypes.func,
  locked: PropTypes.bool,
  // Real §6 capability-graph data for a backed env (tools/flows/personas/
  // guardrails); undefined for a non-backed env (fixture derivation).
  graphData: PropTypes.object,
};

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
