import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
// Actors section (ActorsPanel) is commented out below — it was a dummy cast from
// the fixture ACTOR_LIBRARY, not backed by §6. To be picked up later.
// import ActorsPanel from "./ActorsPanel";
import { CONTRACT_COPY } from "./contract.constants";

// Map the real §6 `end_conditions` onto the episode-card shape. Returns null when
// the environment carries no end conditions yet, and the card says so.
// `ended_reasons` is the vocabulary a finished call may carry (terminate); the
// two limits are the run's truncation ceilings.
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
  };
}

/**
 * The environment contract: how a run ends. The world it runs against (the
 * capability graph, tables and tools) lives on the World tab.
 */
export default function RlContractPanel({ env, backed = false }) {
  // The real §6 `end_conditions`, mapped into the terminate/truncate/clock shape
  // the card renders: `ended_reasons` are the ways a run terminates,
  // `max_turns`/`max_duration_seconds` are the truncation ceilings, `clock` is
  // real-time (voice) or stepped (chat). Null until the build authors them.
  const episode = realEpisode(env);

  return (
    <Box sx={{ p: 2 }}>

      {/* ── run end conditions ────────────────────────────────────────── */}
      <SectionCard
        title={CONTRACT_COPY.endConditions.title}
        subtitle={CONTRACT_COPY.endConditions.subtitle}
        sx={{ mb: 2 }}
      >
        {!episode ? (
          <Box sx={{ px: 2.5, py: 2.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {CONTRACT_COPY.endConditions.notAuthored}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {backed
                ? CONTRACT_COPY.endConditions.notAuthoredBuilt
                : CONTRACT_COPY.endConditions.notAuthoredUnbuilt}
            </Typography>
          </Box>
        ) : (
        <>
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
          direction="row"
          spacing={1.75}
          alignItems="flex-start"
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Iconify icon="solar:clock-circle-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Clock · {episode.clock.mode}</Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{episode.clock.note}</Typography>
          </Box>
        </Stack>
        </>
        )}
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
  // A built (harness) environment; a template or fork is never built.
  backed: PropTypes.bool,
};

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
