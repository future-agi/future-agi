import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Dialog, Box, Stack, Typography, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { twinById } from "../_mock/twins";

const TWIN_TINT = "#7857FC";
const SUCCESS = "#16A34A";

/**
 * Four-step twin sandbox handshake. Each phase advances after its
 * dwell time (kept short so the prototype resolves in ~3s total)
 * and calls `onDone` when the last one lands.
 *
 * The steps mirror the `handshake` array on the "twin_backed" agent
 * type in agentTypes.js so the same story reads in both places:
 *
 *   1. Provisioning twin services  — per-service check turns green
 *   2. Seeding starting state      — indeterminate bar then filled
 *   3. Rotating per-run credentials — masked → rotated
 *   4. Streaming first probe       — per-service latency lights up
 *
 * The modal is non-dismissible: closing before ready would leave the
 * sandbox half-provisioned. The parent controls open state.
 */
const PHASES = [
  {
    key: "provision",
    title: "Provisioning twin services",
    subtitle: "Spinning up the sandbox instance for each service",
    dwell: 1200,
  },
  {
    key: "seed",
    title: "Seeding starting state",
    subtitle: "Resolving the seed prompt into concrete service state",
    dwell: 900,
  },
  {
    key: "rotate",
    title: "Rotating per-run credentials",
    subtitle: "Minting fresh service tokens for this environment",
    dwell: 700,
  },
  {
    key: "probe",
    title: "Streaming first probe",
    subtitle: "Confirming each service responds",
    dwell: 900,
  },
];

export default function TwinProvisioningModal({ open, services, onDone }) {
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [servicesDone, setServicesDone] = useState({});

  useEffect(() => {
    if (!open) {
      setPhaseIdx(0);
      setServicesDone({});
      return undefined;
    }
    let cancelled = false;
    /*
      Per-service ticks inside each phase — stagger the "green"
      markers so the reader watches the checks light up one after
      another instead of all at once. Delay per service is a
      fraction of the phase dwell.
    */
    const perServiceDelay = Math.max(80, Math.floor(PHASES[phaseIdx].dwell / (services.length + 1)));
    services.forEach((sId, i) => {
      const timer = setTimeout(() => {
        if (cancelled) return;
        setServicesDone((prev) => ({ ...prev, [`${phaseIdx}:${sId}`]: true }));
      }, (i + 1) * perServiceDelay);
      return timer;
    });
    /* Advance to the next phase after this one's dwell. */
    const advance = setTimeout(() => {
      if (cancelled) return;
      if (phaseIdx === PHASES.length - 1) {
        onDone?.();
      } else {
        setPhaseIdx((idx) => idx + 1);
      }
    }, PHASES[phaseIdx].dwell);
    return () => {
      cancelled = true;
      clearTimeout(advance);
    };
  }, [open, phaseIdx, services, onDone]);

  return (
    <Dialog
      open={open}
      /* No onClose — closing mid-flight leaves the sandbox half-set. */
      disableEscapeKeyDown
      PaperProps={{
        sx: {
          borderRadius: 2, maxWidth: 560, width: "100%",
          bgcolor: "background.paper", backgroundImage: "none",
          border: "1px solid", borderColor: "divider",
        },
      }}
    >
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
          <Box sx={{
            width: 32, height: 32, borderRadius: 1,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: TWIN_TINT,
          }}>
            <Iconify icon="solar:server-square-bold" width={17} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 700 }}>Provisioning twin sandbox</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Setting up {services.length} service{services.length === 1 ? "" : "s"} · this normally takes ~30s
            </Typography>
          </Box>
        </Stack>

        <Stack spacing={1.5}>
          {PHASES.map((phase, i) => {
            const state = i < phaseIdx ? "done" : i === phaseIdx ? "active" : "pending";
            return (
              <PhaseRow
                key={phase.key}
                phase={phase} state={state}
                services={state === "pending" ? [] : services}
                servicesDone={servicesDone}
                phaseIdx={i}
              />
            );
          })}
        </Stack>

        <LinearProgress
          variant="determinate"
          value={((phaseIdx + 1) / PHASES.length) * 100}
          sx={{
            mt: 2.5, height: 4, borderRadius: 2,
            bgcolor: "background.neutral",
            "& .MuiLinearProgress-bar": { bgcolor: TWIN_TINT, borderRadius: 2 },
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1, textAlign: "center" }}>
          Prototype resolves quickly · production streams live provisioning steps
        </Typography>
      </Box>
    </Dialog>
  );
}
TwinProvisioningModal.propTypes = {
  open: PropTypes.bool,
  services: PropTypes.array,
  onDone: PropTypes.func,
};

function PhaseRow({ phase, state, services, servicesDone, phaseIdx }) {
  const tint = state === "done" ? SUCCESS : state === "active" ? TWIN_TINT : "text.disabled";
  return (
    <Box sx={{
      p: 1.5, borderRadius: 1.25, border: "1px solid",
      borderColor: state === "active" ? alpha(TWIN_TINT, 0.35) : "divider",
      bgcolor: (t) => state === "active"
        ? alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.06 : 0.03)
        : "background.paper",
      transition: "border-color .25s ease, background-color .25s ease",
    }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box sx={{ mt: "1px", flexShrink: 0 }}>
          {state === "done" ? (
            <Iconify icon="solar:check-circle-bold" width={15} sx={{ color: SUCCESS }} />
          ) : state === "active" ? (
            <Iconify
              icon="solar:refresh-circle-linear"
              width={15}
              sx={{
                color: TWIN_TINT,
                animation: "spin 1.2s linear infinite",
                "@keyframes spin": { to: { transform: "rotate(360deg)" } },
              }}
            />
          ) : (
            <Iconify icon="solar:circle-linear" width={15} sx={{ color: "text.disabled" }} />
          )}
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography sx={{
            typography: "s2", fontWeight: 700,
            color: state === "pending" ? "text.subtitle" : "text.primary",
          }}>
            {phase.title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {phase.subtitle}
          </Typography>

          {(state === "active" || state === "done") && services.length > 0 && (
            <Stack
              direction="row" flexWrap="wrap" useFlexGap
              spacing={0.75} sx={{ mt: 0.75 }}
            >
              {services.map((sId) => {
                const t = twinById(sId);
                const done = state === "done" || servicesDone[`${phaseIdx}:${sId}`];
                return (
                  <Stack
                    key={sId} direction="row" alignItems="center" spacing={0.5}
                    sx={{
                      px: 0.75, py: 0.25, borderRadius: 0.75,
                      border: "1px solid",
                      borderColor: (th) => done
                        ? alpha(SUCCESS, 0.35)
                        : th.palette.divider,
                      bgcolor: (th) => done
                        ? alpha(SUCCESS, th.palette.mode === "dark" ? 0.14 : 0.06)
                        : "background.paper",
                      transition: "background-color .25s ease, border-color .25s ease",
                    }}
                  >
                    <Iconify
                      icon={done ? "solar:check-circle-bold" : (t?.icon || "solar:server-square-linear")}
                      width={done ? 11 : 12}
                      sx={done ? { color: SUCCESS } : undefined}
                    />
                    <Typography sx={{
                      typography: "s3", fontWeight: 700,
                      color: done ? SUCCESS : (t?.color || TWIN_TINT),
                    }}>
                      {t?.name || sId}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
          )}
        </Box>
        {state === "done" && (
          <Typography sx={{
            typography: "s3", fontWeight: 700, color: SUCCESS,
            textTransform: "uppercase", letterSpacing: 0.5, flexShrink: 0, mt: "2px",
          }}>
            Done
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
PhaseRow.propTypes = {
  phase: PropTypes.object,
  state: PropTypes.oneOf(["pending", "active", "done"]),
  services: PropTypes.array,
  servicesDone: PropTypes.object,
  phaseIdx: PropTypes.number,
  tint: PropTypes.string,
};
