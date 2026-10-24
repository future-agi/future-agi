import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../../store";
import {
  DEFAULT_TASK_PLAN,
  personaTypeFor,
  planSummary,
  scenariosForPersonas,
  usesPersonaLibrary,
} from "../../_mock/personaScenarios";
import PersonaTaskPlan from "../../components/PersonaTaskPlan";
import { unanswerableNote } from "../../_mock/addScenarios";

const typeOf = (p) => p.simulationType ?? p.simulation_type ?? null;

/**
 * Use library personas in an environment. A persona lives on the scenarios
 * written for it, so this writes the environment's own tasks met by these
 * callers — the environment and its runs then treat them like any other
 * scenario.
 */
export default function UseInEnvironmentDialog({
  open,
  onClose,
  personas,
  envId: fixedEnvId,
}) {
  const { state, dispatch } = useSimStore();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  /* Only environments with a caller — a browser or API agent has no one on
     the other end for a persona to play. */
  const allEnvs = state.myEnvironments || [];
  const envs = allEnvs.filter(usesPersonaLibrary);
  const hiddenCount = allEnvs.length - envs.length;
  const [envId, setEnvId] = useState(fixedEnvId || envs[0]?.id || "");
  const [plan, setPlan] = useState(DEFAULT_TASK_PLAN);

  useEffect(() => {
    if (!open) return;
    setEnvId(fixedEnvId || envs[0]?.id || "");
    setPlan(DEFAULT_TASK_PLAN);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Picked tasks belong to one environment — switching clears them. */
  const chooseEnv = (id) => {
    setEnvId(id);
    setPlan((p) => ({ ...p, taskKeys: [] }));
  };

  const env = envs.find((e) => e.id === envId);
  const wantType = personaTypeFor(env);
  /* A voice persona in a chat environment (or the reverse) still works as a
     brief, but its voice settings won't apply — say so rather than block. */
  const mismatched = useMemo(
    () =>
      wantType
        ? personas.filter((p) => typeOf(p) && typeOf(p) !== wantType)
        : [],
    [personas, wantType],
  );
  const envState = state.byEnv[envId];
  const summary = planSummary(env, envState, personas, plan);
  const total = summary?.total || 0;
  const names = personas.map((p) => p.name);

  const add = () => {
    const batch = scenariosForPersonas(env, envState, personas, plan);
    dispatch({
      type: "patchEnvState",
      envId,
      patch: { scenarios: [...(envState?.scenarios || []), ...batch.rows] },
    });
    onClose();
    enqueueSnackbar(
      `Added ${total} scenario${total === 1 ? "" : "s"} to ${env.name}`,
      {
        variant: "success",
        action: () => (
          <Button
            size="small"
            onClick={() =>
              navigate(
                `${paths.dashboard.simulate.environmentDetail(envId)}/scenarios`,
              )
            }
            sx={{ color: "inherit" }}
          >
            View scenarios
          </Button>
        ),
      },
    );
    if (batch.needing)
      enqueueSnackbar(unanswerableNote(batch), {
        variant: "warning",
        autoHideDuration: 7000,
      });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 1 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {personas.length === 1
            ? `Use ${personas[0].name} in an environment`
            : `Use ${personas.length} personas in an environment`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {"The environment supplies the tasks; we write each one in "}
          {personas.length === 1 ? "this persona's" : "each persona's"}
          {" voice — their opening line, manner and tone."}
        </Typography>
      </Box>

      {envs.length === 0 ? (
        <Typography
          sx={{ px: 3, py: 2, typography: "s2", color: "text.secondary" }}
        >
          {hiddenCount
            ? "None of your environments has a caller — personas play the person on the other end of a voice or chat agent."
            : "Build an environment first — then its scenarios can use library personas."}
        </Typography>
      ) : (
        <Stack spacing={2} sx={{ px: 3, py: 2 }}>
          {personas.length > 1 && (
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
              {names.slice(0, 6).map((n) => (
                <Chip key={n} size="small" variant="outlined" label={n} />
              ))}
              {names.length > 6 && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    alignSelf: "center",
                  }}
                >{`+${names.length - 6} more`}</Typography>
              )}
            </Stack>
          )}
          <Box>
            <Label>Environment</Label>
            <Select
              size="small"
              fullWidth
              value={envId}
              onChange={(e) => chooseEnv(e.target.value)}
              disabled={!!fixedEnvId}
              sx={{ typography: "s2" }}
            >
              {envs.map((e) => (
                <MenuItem key={e.id} value={e.id} sx={{ typography: "s2" }}>
                  {e.name}
                  <Box component="span" sx={{ color: "text.subtitle", ml: 1 }}>
                    {e.surface === "voice"
                      ? "Voice"
                      : e.surface === "chat"
                        ? "Chat"
                        : e.surface || ""}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </Box>
          <PersonaTaskPlan
            labels
            env={env}
            envState={envState}
            plan={plan}
            onChange={setPlan}
          />
          {mismatched.length > 0 && (
            <Alert
              severity="warning"
              icon={<Iconify icon="solar:info-circle-linear" width={16} />}
            >
              {`${mismatched.map((p) => p.name).join(", ")} ${mismatched.length === 1 ? "is a" : "are"} ${wantType === "voice" ? "chat" : "voice"} persona${mismatched.length === 1 ? "" : "s"} — ${env?.name} is a ${wantType === "voice" ? "voice" : "chat"} environment, so ${mismatched.length === 1 ? "its" : "their"} ${wantType === "voice" ? "typing" : "voice"} settings won't apply.`}
            </Alert>
          )}
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {summary
              ? `${summary.text}. Added to ${env?.name || "the environment"}, marked “From persona library” — edit or remove them on its Scenarios tab.`
              : null}
          </Typography>
        </Stack>
      )}

      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!env || !total}
          onClick={add}
        >
          {`Add ${total} scenario${total === 1 ? "" : "s"}`}
        </Button>
      </Stack>
    </Dialog>
  );
}

UseInEnvironmentDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  personas: PropTypes.array.isRequired,
  envId: PropTypes.string,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
