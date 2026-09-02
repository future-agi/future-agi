import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import {
  adapterOf, modalityMovedBy, observationSpace, actionSpace,
  transitionDynamics, rewardSpec, VERIFIER_KINDS, episodeContract, contractParts,
} from "../_mock/rlContract";
import { setupGaps, gapCounts, GAP_STATUS } from "../_mock/setupGaps";
import { getAgentType } from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";

/**
 * The RL environment contract.
 *
 * The keystone: everything read from the agent lands here, and everything
 * downstream — running, scoring, training — reads from here. Five parts, in
 * the order they have to be settled, and then whatever could not be settled
 * without asking you.
 */
export default function RlContractPanel({ env, envState, onGo, buildMode }) {
  const adapter = adapterOf(env, envState);
  const movedBy = modalityMovedBy(env, envState);
  const agentType = getAgentType(envState.agent?.typeId);

  const gaps = setupGaps(env, envState);
  const counts = gapCounts(gaps);
  /* Route the "Fix gaps" button to whichever tab owns the top
     outstanding gap. Grading gaps go to Evaluations; contract-shape
     gaps stay here. */
  const topBlockingArea = gaps.find((g) => g.status === "blocking")?.area || null;
  const parts = contractParts(env, envState);
  const done = parts.filter((p) => p.done).length;

  const obs = observationSpace(env, adapter);
  const acts = actionSpace(env, adapter);
  const dynamics = transitionDynamics(env, envState);
  const reward = rewardSpec(env);
  const episode = episodeContract(env, envState);

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>Environment contract</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
            What makes this an environment rather than a test script. Everything read from your
            agent compiles into these five parts, and everything downstream reads them back.
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{done}/{parts.length} compiled</Typography>
          <Box sx={{ width: 90, height: 5, borderRadius: 3, bgcolor: "background.neutral", overflow: "hidden" }}>
            <Box sx={{ width: `${(done / parts.length) * 100}%`, height: "100%", bgcolor: "primary.main" }} />
          </Box>
        </Stack>
      </Stack>

      {/* ── 1 · adapter ── */}
      <Part
        n={1}
        title="Modality adapter"
        blurb="Fixes what an observation and an action are. Everything else is generic."
      >
        {/*
          Stated, not asked.

          The modality is not a knob on the contract — it is a consequence of
          which agent is connected. A picker here re-opened a question the
          Agent page already settled, and it only rewrote this page and the
          fidelity controls, so you could put a coding adapter on a phone line
          while the scenarios, tools and runtime connection stayed voice.

          Correction still exists; it lives where the cause lives. Changing
          the agent type on the Agent page moves the modality and everything
          derived from it together — and the Agent step is one click away in
          the rail, so restating it as a button here is a second door to a
          room you can already see.
        */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 2 }}>
          <Box
            sx={{
              width: 34, height: 34, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
              color: adapter.color,
              bgcolor: (t) => alpha(adapter.color, t.palette.mode === "dark" ? 0.16 : 0.1),
            }}
          >
            <Iconify icon={adapter.icon} width={17} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.875} flexWrap="wrap">
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>{adapter.label}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {agentType ? `· fixed by ${agentType.label}` : "· from this environment's surface"}
              </Typography>
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{adapter.blurb}</Typography>
          </Box>
        </Stack>

        <Stack
          direction="row" spacing={1.25} alignItems="flex-start"
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Iconify
            icon={movedBy ? "solar:danger-triangle-linear" : "solar:info-circle-linear"}
            width={15}
            sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {movedBy
              ? `This environment was built for ${getSurface(env.surface)?.label || env.surface}, but ${movedBy.label} is a ${adapter.label.toLowerCase()} agent — so the spaces below, the fidelity controls and the runtime connection follow the agent, not the template. Reconnect a matching agent if that was not deliberate.`
              : "Everything below is derived from this. Connecting a different kind of agent rewrites the observation and action spaces, the fidelity controls and the runtime connection together — so the modality changes on the Agent step, not here."}
          </Typography>
        </Stack>
      </Part>

      {/* ── 2 · spaces ── */}
      <Part
        n={2}
        title="Observation and action spaces"
        blurb="Four fields are the same in every modality. The adapter fills the rest — which is why one runner serves all six."
      >
        <Box sx={{
          display: "grid", gap: "1px",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.07),
        }}>
          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>Observation</Label>
            <Stack spacing={1}>
              {obs.map((o) => (
                <Stack key={o.field} direction="row" spacing={1.25} alignItems="flex-start">
                  <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", fontWeight: 600, width: 108, flexShrink: 0 }}>
                    {o.field}
                  </Typography>
                  <Box minWidth={0}>
                    <Stack direction="row" spacing={0.75} alignItems="center">
                      <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.subtitle" }}>
                        {o.type}
                      </Typography>
                      <Chip
                        size="small"
                        label={o.generic ? "core" : adapter.label.toLowerCase()}
                        sx={{
                          height: 16, borderRadius: 0.5,
                          color: o.generic ? "text.subtitle" : adapter.color,
                          border: "1px solid", borderColor: o.generic ? "divider" : alpha(adapter.color, 0.4),
                          bgcolor: "transparent",
                          "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 600 },
                        }}
                      />
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{o.filled}</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Box>

          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>Action</Label>
            <Stack spacing={1}>
              {acts.map((a) => (
                <Stack key={a.verb} direction="row" spacing={1.25} alignItems="flex-start">
                  <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", fontWeight: 600, width: 90, flexShrink: 0 }}>
                    {a.verb}
                  </Typography>
                  <Box minWidth={0}>
                    <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.subtitle" }}>
                      {a.args}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{a.note}</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Box>
      </Part>

      {/* ── 3 · dynamics ── */}
      <Part n={3} title="Transition dynamics" blurb="What moves the world between steps — and it is never only the agent.">
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {dynamics.map((d) => (
            <Stack key={d.id} direction="row" alignItems="flex-start" spacing={1.75} sx={{ px: 2.5, py: 1.75 }}>
              <Iconify icon={d.icon} width={17} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography sx={{ typography: "s2", fontWeight: 700 }}>{d.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>· {d.value}</Typography>
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{d.note}</Typography>
              </Box>
              <Button
                size="small"
                onClick={() => onGo(d.to)}
                sx={{ typography: "s2", fontWeight: 700, color: "primary.main", flexShrink: 0, minWidth: 0 }}
              >
                Open
              </Button>
            </Stack>
          ))}
        </Stack>
      </Part>

      {/* ── 4 · reward ── */}
      <Part
        n={4}
        title="Reward spec"
        blurb="The verifiers ARE the evals, given weights. If reward had its own definition you could train an agent that scores well and still fails the gate."
      >
        {/*
          Gap-fill divider trick: grid container is tinted so a 1px
          `gap` between columns shows through as a solid divider line.
          Preferred over 1px borders because a single tinted fill
          renders identically on all sides — no semi-transparent
          border rendering differently against paper vs the outer bg.
        */}
        <Box
          sx={{
            display: "grid",
            gap: "1px",
            gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" },
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.07),
          }}
        >
          {VERIFIER_KINDS.map((k) => (
            <Box
              key={k.id}
              sx={{ p: 2.5, bgcolor: "background.paper" }}
            >
              <Stack direction="row" alignItems="center" spacing={0.875} sx={{ mb: 0.5 }}>
                <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: k.color }} />
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{k.label}</Typography>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>{k.blurb}</Typography>
              <Stack spacing={1}>
                {reward[k.id === "constraint" ? "constraint" : k.id].map((v, j) => (
                  <Box key={`${v.name}-${j}`}>
                    <Stack direction="row" alignItems="baseline" spacing={1}>
                      <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", fontWeight: 600 }}>
                        {v.name}
                      </Typography>
                      <Typography sx={{ typography: "s2", fontWeight: 700, color: v.weight < 0 ? "#DC2626" : "text.primary", fontVariantNumeric: "tabular-nums" }}>
                        {v.weight > 0 ? "+" : ""}{v.weight.toFixed(2)}
                      </Typography>
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{v.note}</Typography>
                  </Box>
                ))}
              </Stack>
            </Box>
          ))}
        </Box>
        <Stack
          direction="row" spacing={1.25} alignItems="center"
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>
            {buildMode
              ? "Weights become editable after your first run, on the RL interface. Verifiers are the evals applied to this environment."
              : "Weights are editable on the interface, and the verifiers themselves are the evals applied to this environment."}
          </Typography>
          {!buildMode && (
            <Button size="small" onClick={() => onGo?.("rl")} sx={{ typography: "s2", fontWeight: 700, color: "primary.main", flexShrink: 0 }}>
              Interface
            </Button>
          )}
        </Stack>
      </Part>

      {/* ── 5 · episode ── */}
      <Part n={5} title="Episode contract" blurb="When a run is over, and which kind of over it was.">
        <Box sx={{
          display: "grid", gap: "1px",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.07),
        }}>
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
              Not a failure, and not terminal — bootstrap from the last state.
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
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />} sx={{ borderTop: "1px solid", borderColor: "divider" }}>
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
      </Part>

      {/*
        Gaps summary. The old "Open inbox" CTA pointed to a
        Needs-your-input tab that was removed — the blocking-gap
        surface now lives on the tab whose area the gap belongs to
        (Evaluations for grading, this tab for contract). The action
        now routes the user to whichever tab actually owns the top
        outstanding gap, or hides itself when nothing is blocking.
      */}
      <SectionCard
        title="Unresolved contract fields"
        subtitle="What compiling could not settle on its own"
        action={
          counts.blocking > 0 && (
            <Button
              size="small"
              onClick={() => onGo?.(topBlockingArea === "Grading" ? "evals" : "contract")}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: 700, color: "primary.main" }}
            >
              {topBlockingArea === "Grading" ? "Fix in Evaluations" : "Review below"}
            </Button>
          )
        }
      >
        <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, flexWrap: "wrap", rowGap: 1.5 }}>
          {["blocking", "assumed", "resolved"].map((k) => (
            <Stack key={k} direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ typography: "m1", fontWeight: 700, color: counts[k] ? GAP_STATUS[k].color : "text.subtitle" }}>
                {counts[k]}
              </Typography>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>{GAP_STATUS[k].label}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{GAP_STATUS[k].blurb}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </SectionCard>
    </Box>
  );
}

RlContractPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  onGo: PropTypes.func,
  buildMode: PropTypes.bool,
};

/* ── a numbered part ─────────────────────────────────────────────────────── */

function Part({ n, title, blurb, children }) {
  return (
    <SectionCard
      sx={{ mb: 2 }}
      title={
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Box
            sx={{
              width: 20, height: 20, borderRadius: "50%", display: "grid", placeItems: "center", flexShrink: 0,
              color: "primary.main", bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
              typography: "s3", fontWeight: 700,
            }}
          >
            {n}
          </Box>
          <span>{title}</span>
        </Stack>
      }
      subtitle={blurb}
    >
      {children}
    </SectionCard>
  );
}
Part.propTypes = { n: PropTypes.number, title: PropTypes.string, blurb: PropTypes.string, children: PropTypes.node };

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 1 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
