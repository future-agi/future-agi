import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  adapterOf, observationSpace, actionSpace, transitionDynamics,
  rewardSpec, episodeContract, contractParts,
} from "src/api/simulate-environments/_fixtures/rlContract";
import { setupGaps, gapCounts, GAP_STATUS } from "src/api/simulate-environments/_fixtures/setupGaps";
import { GAP_AREA_TO_TAB } from "../workspace.constants";
import SectionCard from "../../components/SectionCard";
import { Part } from "./ContractPart";
import ContractSpaces from "./ContractSpaces";
import ContractRewardEpisode from "./ContractRewardEpisode";
import ActorsPanel from "./ActorsPanel";
import { CONTRACT_COPY, GAP_LEGEND_ORDER } from "./contract.constants";

/**
 * The RL environment contract — the keystone tab. Everything read from the
 * agent compiles into five parts, and everything downstream reads them back.
 *
 * Deviations from the prototype panel:
 *  - The modality is resolved purely from the environment surface (the ported
 *    fixture dropped the cross-surface agent override), so the adapter subtitle
 *    and info line are always the surface-derived branch.
 *  - Actor editing and removal require a `patch` channel the contract tab does
 *    not expose, so the actors slot renders the cast read-only.
 */
export default function RlContractPanel({ env, envState, onGo, buildMode }) {
  const adapter = adapterOf(env);

  const gaps = setupGaps(env, envState);
  const counts = gapCounts(gaps);
  // Route the "Fix gaps" action to whichever tab owns the top outstanding
  // gap — Grading gaps go to Evaluations, contract-shape gaps stay here.
  const topBlockingArea = gaps.find((g) => g.status === "blocking")?.area || null;
  const parts = contractParts(env, envState);
  const done = parts.filter((p) => p.done).length;

  const obs = observationSpace(env, adapter);
  const acts = actionSpace(env, adapter);
  const dynamics = transitionDynamics(env, envState);
  const reward = rewardSpec(env);
  const episode = episodeContract(env);

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "flex-end" }} spacing={2} sx={{ mb: 2 }}>
        <Box flex={1}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>{CONTRACT_COPY.heading}</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>
            {CONTRACT_COPY.headingBlurb}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{CONTRACT_COPY.compiled(done, parts.length)}</Typography>
          <Box sx={{ width: 90, height: 5, borderRadius: 3, bgcolor: "background.neutral", overflow: "hidden" }}>
            <Box sx={{ width: `${(done / parts.length) * 100}%`, height: "100%", bgcolor: "primary.main" }} />
          </Box>
        </Stack>
      </Stack>

      <Part n={1} title={CONTRACT_COPY.parts.adapter.title} blurb={CONTRACT_COPY.parts.adapter.blurb}>
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
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>{adapter.label}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{CONTRACT_COPY.adapterSurfaceNote}</Typography>
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{adapter.blurb}</Typography>
          </Box>
        </Stack>
        <Stack
          direction="row" spacing={1.25} alignItems="flex-start"
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{CONTRACT_COPY.adapterDerivedNote}</Typography>
        </Stack>
      </Part>

      <Part n={2} title={CONTRACT_COPY.parts.spaces.title} blurb={CONTRACT_COPY.parts.spaces.blurb}>
        <ContractSpaces obs={obs} acts={acts} adapter={adapter} />
      </Part>

      <Part n={3} title={CONTRACT_COPY.parts.dynamics.title} blurb={CONTRACT_COPY.parts.dynamics.blurb}>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {dynamics.map((d) => (
            <Stack key={d.id} direction="row" alignItems="flex-start" spacing={1.75} sx={{ px: 2.5, py: 1.75 }}>
              <Iconify icon={d.icon} width={17} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
              <Box flex={1} minWidth={0}>
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{d.label}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>· {d.value}</Typography>
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{d.note}</Typography>
              </Box>
              <Button
                size="small"
                onClick={() => onGo?.(d.to)}
                sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "primary.main", flexShrink: 0, minWidth: 0 }}
              >
                {CONTRACT_COPY.openAction}
              </Button>
            </Stack>
          ))}
        </Stack>
      </Part>

      <ContractRewardEpisode reward={reward} episode={episode} buildMode={buildMode} onGo={onGo} />

      <SectionCard
        title={CONTRACT_COPY.gapsTitle}
        subtitle={CONTRACT_COPY.gapsSubtitle}
        action={
          counts.blocking > 0 && (
            <Button
              size="small"
              onClick={() => onGo?.(GAP_AREA_TO_TAB[topBlockingArea] || "contract")}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
              sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "primary.main" }}
            >
              {topBlockingArea === "Grading" ? CONTRACT_COPY.gapsFixGrading : CONTRACT_COPY.gapsReviewHere}
            </Button>
          )
        }
      >
        <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, flexWrap: "wrap", rowGap: 1.5 }}>
          {GAP_LEGEND_ORDER.map((k) => (
            <Stack key={k} direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ typography: "m1", fontWeight: "fontWeightBold", color: counts[k] ? GAP_STATUS[k].color : "text.subtitle" }}>
                {counts[k]}
              </Typography>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{GAP_STATUS[k].label}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{GAP_STATUS[k].blurb}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </SectionCard>

      {/*
        Actors — the other parties the environment applies against a run. The
        cast is read from the environment and rendered read-only here.
      */}
      <Box sx={{ mt: 3 }}>
        <ActorsPanel env={env} envState={envState} onGo={onGo} />
      </Box>
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
  evals: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  actors: PropTypes.arrayOf(PropTypes.string),
  gapsResolved: PropTypes.objectOf(PropTypes.string),
});

RlContractPanel.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE.isRequired,
  onGo: PropTypes.func,
  buildMode: PropTypes.bool,
};
