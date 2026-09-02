import { useMemo, useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, Collapse,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore, useEnvState } from "../store";
import { getAgentType } from "../_mock/agentTypes";
import { checkCompatibility } from "../_mock/compatibility";
import { SectionCard, EmptyState } from "../components/primitives";
import TwinLogo from "../components/TwinLogo";
import { twinById } from "../_mock/twins";
import DynamicField from "../workspace/connect/DynamicField";
import FitCheckDialog from "./FitCheckDialog";

/**
 * Post-provision Connect step for the compose flow.
 *
 * The env is already adopted (useCreateTwinEnv did that) — this page
 * collects the agent's SDK endpoint + auth + identity map, runs the
 * scripted fit-check probe, then hands the user off to the twin-review
 * layout (chat left + workspace tabs right).
 *
 * Mirrors step 0 of the template flow in UseTemplate so a user landing
 * here recognises the shape immediately.
 */
export default function TwinConnectPage() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const { state } = useSimStore();
  const { patch } = useEnvState(envId);
  const env = state.myEnvironments.find((e) => e.id === envId);

  const type = useMemo(() => getAgentType("twin_backed"), []);
  const [values, setValues] = useState({});
  const [showMore, setShowMore] = useState(false);
  const [fitCheckOpen, setFitCheckOpen] = useState(false);

  /* Seed the values map with each field's defaultValue when the type
     resolves — same pattern UseTemplate uses. */
  useEffect(() => {
    if (!type) return;
    const defaults = {};
    (type.fields || []).forEach((f) => {
      if (f.defaultValue !== undefined) defaults[f.key] = f.defaultValue;
    });
    if (Object.keys(defaults).length) setValues((prev) => ({ ...defaults, ...prev }));
  }, [type]);

  if (!env) {
    return (
      <Box sx={{ p: 4 }}>
        <EmptyState icon="solar:danger-triangle-linear" title="Environment not found" body="It may have been renamed or deleted." />
      </Box>
    );
  }

  const required = (type?.fields || []).filter((f) => f.required);
  const optional = (type?.fields || []).filter((f) => !f.required);
  const missing = required.filter((f) => !values[f.key]);
  const services = env.twinBacking?.services || [];
  const fit = checkCompatibility(env);

  const commitAgent = () => {
    /* Patch the env with the agent connection so the workspace's
       Agents tab reflects it as the source agent. */
    patch({
      agent: {
        typeId: type?.id,
        values,
        via: "endpoint",
        connectedAt: new Date().toISOString(),
      },
    });
    navigate(paths.dashboard.simulate.environmentTwinReview(envId));
  };

  return (
    <>
      <Helmet><title>Connect your agent · {env.name} | Future AGI</title></Helmet>
      <Box sx={{ p: 3 }}>
        {/* header */}
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
          <Tooltip arrow title="Back to environments">
            <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environments)}>
              <Iconify icon="solar:alt-arrow-left-linear" width={18} />
            </IconButton>
          </Tooltip>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Connect your agent
            </Typography>
            <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
              {env.name} is provisioned. Point your agent at the sandbox — the fit-check probes it before you land in the workspace.
            </Typography>
          </Box>
        </Stack>

        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", lg: "1.55fr 1fr" } }}>
          {/* ── left: connect form ── */}
          <SectionCard
            title="Connect via SDK endpoint"
            subtitle="Your agent calls into the clone sandbox. We handle auth rotation per run."
          >
            <Stack spacing={2.25} sx={{ p: 2.5 }}>
              {required.map((f) => (
                <DynamicField
                  key={f.key} field={f} value={values[f.key]} values={values}
                  onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                />
              ))}

              {optional.length > 0 && (
                <Box>
                  <Button
                    size="small"
                    onClick={() => setShowMore((o) => !o)}
                    startIcon={<Iconify icon={showMore ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={14} />}
                    sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", px: 0.5 }}
                  >
                    {showMore ? "Hide" : `${optional.length} more, usually detected`}
                  </Button>
                  <Collapse in={showMore} unmountOnExit>
                    <Stack spacing={2.25} sx={{ mt: 2 }}>
                      {optional.map((f) => (
                        <DynamicField
                          key={f.key} field={f} value={values[f.key]} values={values}
                          onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                        />
                      ))}
                    </Stack>
                  </Collapse>
                </Box>
              )}
            </Stack>

            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
              <Button
                variant="contained" color="primary"
                disabled={missing.length > 0}
                onClick={() => setFitCheckOpen(true)}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                Check it fits
              </Button>
              {missing.length > 0 && (
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {missing.length} required {missing.length === 1 ? "field" : "fields"} left
                </Typography>
              )}
            </Stack>
          </SectionCard>

          {/* ── right: what you're connecting to ── */}
          <Stack spacing={2}>
            <SectionCard title="What you're connecting to" subtitle="Live sandbox, per-run credentials, real SDK shape">
              <Stack sx={{ px: 2.5, py: 2 }} spacing={1.5}>
                <Line label="Environment" value={env.name} />
                <Line label="Services" value={`${services.length} clone${services.length === 1 ? "" : "s"}`} />
                <Box>
                  <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}>
                    Twins
                  </Typography>
                  <Stack spacing={0.75}>
                    {services.map((sid) => {
                      const twin = twinById(sid);
                      return (
                        <Stack key={sid} direction="row" alignItems="center" spacing={1}
                          sx={{
                            px: 1, py: 0.75, borderRadius: 0.75,
                            border: "1px solid", borderColor: "divider",
                          }}>
                          <TwinLogo twin={twin} width={16} />
                          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>{twin?.name || sid}</Typography>
                          <Typography sx={{
                            typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                            color: "text.subtitle",
                          }} noWrap>
                            {env.twinBacking?.endpoints?.[sid]?.replace(/^https?:\/\//, "")}
                          </Typography>
                        </Stack>
                      );
                    })}
                  </Stack>
                </Box>
              </Stack>
            </SectionCard>

            <Box sx={{
              p: 2, borderRadius: 1.25, border: "1px solid",
              borderColor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.4 : 0.3),
              bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.08 : 0.04),
            }}>
              <Stack direction="row" spacing={1.25} alignItems="flex-start">
                <Iconify icon="solar:shield-keyhole-linear" width={16} sx={{ color: "#16A34A", flexShrink: 0, mt: "1px" }} />
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                  <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>Nothing touches production.</Box>{" "}
                  Test credentials rotate per run; the sandbox is torn down when the env expires or you reset state.
                </Typography>
              </Stack>
            </Box>
          </Stack>
        </Box>
      </Box>

      <FitCheckDialog
        open={fitCheckOpen}
        probeSteps={fit?.probe || []}
        onDone={() => {
          setFitCheckOpen(false);
          commitAgent();
        }}
      />
    </>
  );
}

function Line({ label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <Typography sx={{
        typography: "s3", fontWeight: 700, color: "text.subtitle",
        textTransform: "uppercase", letterSpacing: 0.4, minWidth: 96,
      }}>
        {label}
      </Typography>
      <Typography sx={{ typography: "s2", flex: 1 }}>{value}</Typography>
    </Stack>
  );
}
