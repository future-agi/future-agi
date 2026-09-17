import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "src/sections/simulate/environments/buildEnvironment/buildTones";
import { Part, Label } from "./ContractPart";
import { CONTRACT_COPY, VERIFIER_KINDS, MONO_FONT } from "./contract.constants";

const TINTED_GRID = (t) =>
  alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.09 : 0.07);

// Parts 4 and 5 — the reward spec (verifiers grouped by kind, with weights)
// and the episode contract (terminate/truncate, clock and seed).
export default function ContractRewardEpisode({ reward, episode, buildMode, onGo }) {
  return (
    <>
      <Part n={4} title={CONTRACT_COPY.parts.reward.title} blurb={CONTRACT_COPY.parts.reward.blurb}>
        <Box sx={{
          display: "grid", gap: "1px",
          gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" },
          bgcolor: TINTED_GRID,
        }}>
          {VERIFIER_KINDS.map((k) => (
            <Box key={k.id} sx={{ p: 2.5, bgcolor: "background.paper" }}>
              <Stack direction="row" alignItems="center" spacing={0.875} sx={{ mb: 0.5 }}>
                <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: k.color }} />
                <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{k.label}</Typography>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>{k.blurb}</Typography>
              <Stack spacing={1}>
                {(reward[k.id] || []).map((v, j) => (
                  <Box key={`${v.name}-${j}`}>
                    <Stack direction="row" alignItems="baseline" spacing={1}>
                      <Typography sx={{ typography: "s2", fontFamily: MONO_FONT, fontWeight: "fontWeightSemiBold" }}>
                        {v.name}
                      </Typography>
                      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: v.weight < 0 ? BUILD_TONES.red : "text.primary", fontVariantNumeric: "tabular-nums" }}>
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
            {buildMode ? CONTRACT_COPY.rewardBuildNote : CONTRACT_COPY.rewardNote}
          </Typography>
          {!buildMode && (
            <Button size="small" onClick={() => onGo?.("rl")} sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "primary.main", flexShrink: 0 }}>
              {CONTRACT_COPY.interfaceAction}
            </Button>
          )}
        </Stack>
      </Part>

      <Part n={5} title={CONTRACT_COPY.parts.episode.title} blurb={CONTRACT_COPY.parts.episode.blurb}>
        <Box sx={{
          display: "grid", gap: "1px",
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          bgcolor: TINTED_GRID,
        }}>
          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>{CONTRACT_COPY.terminateLabel}</Label>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
              {CONTRACT_COPY.terminateBlurb}
            </Typography>
            <Stack spacing={1}>
              {episode.terminate.map((e) => (
                <Box key={e.when}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{e.when}</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{e.note}</Typography>
                </Box>
              ))}
            </Stack>
          </Box>
          <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
            <Label>{CONTRACT_COPY.truncateLabel}</Label>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>
              {CONTRACT_COPY.truncateBlurb}
            </Typography>
            <Stack spacing={1}>
              {episode.truncate.map((e) => (
                <Box key={e.when}>
                  <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{e.when}</Typography>
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
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{CONTRACT_COPY.clockLabel(episode.clock.mode)}</Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>{episode.clock.note}</Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1.75} sx={{ px: 2.5, py: 1.75 }} alignItems="flex-start">
            <Iconify icon="solar:dice-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>{CONTRACT_COPY.seedTitle}</Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>{episode.seed.note}</Typography>
            </Box>
          </Stack>
        </Stack>
      </Part>
    </>
  );
}

const REWARD_ROW = PropTypes.arrayOf(PropTypes.shape({
  name: PropTypes.string,
  weight: PropTypes.number,
  note: PropTypes.string,
}));

const EPISODE_ENDING = PropTypes.arrayOf(PropTypes.shape({
  when: PropTypes.string,
  note: PropTypes.string,
}));

ContractRewardEpisode.propTypes = {
  reward: PropTypes.shape({
    goal: REWARD_ROW,
    rubric: REWARD_ROW,
    constraint: REWARD_ROW,
  }).isRequired,
  episode: PropTypes.shape({
    terminate: EPISODE_ENDING,
    truncate: EPISODE_ENDING,
    clock: PropTypes.shape({ mode: PropTypes.string, note: PropTypes.string }),
    seed: PropTypes.shape({ note: PropTypes.string }),
  }).isRequired,
  buildMode: PropTypes.bool,
  onGo: PropTypes.func,
};
