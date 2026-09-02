import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Chip, TextField, InputAdornment, Tab,
} from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";
import { TWIN_CATALOG, twinsByCategory, twinById } from "../_mock/twins";
import TwinLogo from "../components/TwinLogo";
import TwinProvisioningModal from "../environments/TwinProvisioningModal";
import { useCreateTwinEnv } from "./useCreateTwinEnv";

const TWIN_TINT = "#7857FC";

/**
 * Twins — the dedicated browse + entry-point surface for twin-backed
 * environments. This is not a parallel storage layer: the actual
 * twin-backed environments still live in `state.myEnvironments`, and
 * every workflow (scenarios, evals, runs, versions) happens under the
 * standard Environments workspace. This page is a marketing + discovery
 * surface for what would otherwise be one-click-deep inside env
 * creation.
 *
 * Three sections top to bottom:
 *
 *   1. Hero — one-sentence value prop + the primary "Create a
 *      twin-backed environment" CTA. Everything on this page routes
 *      into the same NewTwinEnvironment flow the Environments gallery
 *      button uses; two paths, one destination.
 *
 *   2. Browse the catalog — the 16 service twins grouped by category
 *      (Productivity, Communication, CRM, DevTools, Finance, Data).
 *      Each row shows the brand logo, name, blurb, and API-level
 *      capability. Clicking a service jumps into env creation with
 *      that service pre-selected (via `?service=...` query param).
 *
 *   3. My twin-backed environments — filtered view of the user's
 *      envs where twinBacking is set. Empty state nudges them into
 *      the create flow. Each row links to its workspace under
 *      Environments (not a separate detail page here — this is a
 *      view, not a store).
 */
export default function TwinsBrowse() {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const createTwinEnv = useCreateTwinEnv();
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("catalog");
  /*
    One-click provisioning. Clicking a service card sets
    `provisioningServices` to that single service; the modal renders
    and animates the 4-phase handshake; on `onDone` we create the
    env with sensible defaults and land the user straight in the
    workspace. No second picker page in between.
  */
  const [provisioningServices, setProvisioningServices] = useState(null);

  const cats = useMemo(() => twinsByCategory(), []);
  const filteredCats = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return cats;
    return cats.map((c) => ({
      ...c,
      items: c.items.filter((t) =>
        t.name.toLowerCase().includes(query) || t.blurb.toLowerCase().includes(query),
      ),
    })).filter((c) => c.items.length > 0);
  }, [cats, q]);

  const myTwinEnvs = useMemo(
    () => (state.myEnvironments || []).filter((e) => !!state.byEnv?.[e.id]?.twinBacking),
    [state.myEnvironments, state.byEnv],
  );

  const startProvisioning = (serviceId) =>
    setProvisioningServices([serviceId]);

  const finishProvisioning = () => {
    if (!provisioningServices) return;
    createTwinEnv(provisioningServices);
    setProvisioningServices(null);
  };

  const openEnv = (envId) =>
    navigate(paths.dashboard.simulate.twinDetail(envId));

  return (
    <Box sx={{ p: 3 }}>
      {/* ── hero ── */}
      <Stack
        direction={{ xs: "column", md: "row" }}
        alignItems={{ md: "flex-end" }}
        spacing={2}
        sx={{ mb: 1.5 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "m2", fontWeight: 700, mb: 0.5 }}>Clones</Typography>
          <Typography noWrap sx={{ typography: "s1", color: "text.secondary" }}>
            Live SaaS sandboxes your agent acts on — fresh per run, evals grade the end state.
          </Typography>
        </Box>
      </Stack>

      {/* ── tabs ── */}
      <CustomTabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        sx={{
          borderBottom: "1px solid", borderColor: "divider", mb: 2, minHeight: 40,
          "& .MuiTab-root": { typography: "s1" },
        }}
      >
        <Tab value="catalog" label={`Catalog (${TWIN_CATALOG.length})`} sx={{ minHeight: 40 }} />
        <Tab value="mine" label={`Your clones (${myTwinEnvs.length})`} sx={{ minHeight: 40 }} />
      </CustomTabs>

      {tab === "catalog" && (
        <Box sx={{ mb: 2 }}>
          <TextField
            size="small" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search services…"
            InputProps={{
              sx: {
                typography: "s2", height: 32,
                bgcolor: "background.paper",
                "& .MuiOutlinedInput-notchedOutline": {
                  borderColor: (t) => t.palette.mode === "dark"
                    ? "rgba(255,255,255,0.16)"
                    : "rgba(0,0,0,0.15)",
                },
                "&:hover .MuiOutlinedInput-notchedOutline": {
                  borderColor: "text.subtitle",
                },
              },
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="solar:magnifer-linear" width={13} sx={{ color: "text.subtitle" }} />
                </InputAdornment>
              ),
            }}
            sx={{ width: 260, "& .MuiOutlinedInput-input": { py: 0 } }}
          />
        </Box>
      )}

      {tab === "catalog" ? (
        <Stack spacing={2.5}>
          {filteredCats.map((cat) => (
            <Box key={cat.id}>
              <Typography sx={{
                typography: "s3", fontWeight: 700, color: "text.subtitle",
                letterSpacing: 0.5, textTransform: "uppercase", mb: 1,
              }}>
                {cat.label}
              </Typography>
              <Box sx={{
                display: "grid", gap: 1.25,
                gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", lg: "1fr 1fr 1fr" },
              }}>
                {cat.items.map((t) => (
                  <CatalogCard key={t.id} twin={t} onClick={() => startProvisioning(t.id)} />
                ))}
              </Box>
            </Box>
          ))}
          {filteredCats.length === 0 && (
            <Box sx={{ py: 5, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                No services match &ldquo;{q}&rdquo;
              </Typography>
            </Box>
          )}
        </Stack>
      ) : (
        <MyTwinEnvsTabView
          envs={myTwinEnvs}
          state={state}
          onOpen={openEnv}
          onGoCatalog={() => setTab("catalog")}
        />
      )}

      <TwinProvisioningModal
        open={!!provisioningServices}
        services={provisioningServices || []}
        onDone={finishProvisioning}
      />
    </Box>
  );
}

/* ── catalog card ─────────────────────────────────────────────────────── */

function CatalogCard({ twin, onClick }) {
  return (
    <Box
      onClick={onClick}
      sx={{
        p: 1.75, borderRadius: 1.5, cursor: "pointer",
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.paper",
        transition: "border-color .15s ease, background-color .15s ease",
        "&:hover": {
          borderColor: "text.disabled",
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <TwinLogo twin={twin} width={22} />
        <Typography sx={{ typography: "s1", fontWeight: 700, flex: 1, minWidth: 0 }} noWrap>
          {twin.name}
        </Typography>
        <Chip
          size="small"
          label={twin.apiLevel === "api+ui" ? "API + UI" : "API"}
          sx={{
            height: 18, borderRadius: 0.5,
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.secondary",
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700, letterSpacing: 0.3 },
          }}
        />
      </Stack>
      <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.75, lineHeight: 1.45 }}>
        {twin.blurb}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        {twin.depth.slice(0, 4).map((d) => (
          <Typography
            key={d}
            sx={{
              px: 0.75, py: 0.125, borderRadius: 0.5,
              typography: "s3", fontWeight: 600, color: "text.subtitle",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
            }}
          >
            {d}
          </Typography>
        ))}
      </Stack>
    </Box>
  );
}

CatalogCard.propTypes = { twin: PropTypes.object, onClick: PropTypes.func };


/* ── my twin envs section ─────────────────────────────────────────────── */

function MyTwinEnvsTabView({ envs, state, onOpen, onGoCatalog }) {
  if (envs.length === 0) {
    return (
      <Box sx={{
        borderRadius: 1.5, border: "1px dashed", borderColor: "divider",
        p: 4, textAlign: "center",
      }}>
        <Iconify icon="solar:server-square-linear" width={26} sx={{ color: "text.subtitle", mb: 1 }} />
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>No clones provisioned yet</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", maxWidth: 520, mx: "auto", mt: 0.5 }}>
          Pick a service from the catalog and it&apos;s provisioned in one click — no config, no waiting.
        </Typography>
        <Button
          variant="contained" color="primary" size="small"
          onClick={onGoCatalog}
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          sx={{ typography: "s2", fontWeight: 700, mt: 2 }}
        >
          Browse catalog
        </Button>
      </Box>
    );
  }

  return (
    <Box>
      <Box sx={{
        display: "grid", gap: 1.25,
        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
      }}>
        {envs.map((env) => {
          const backing = state.byEnv?.[env.id]?.twinBacking;
          const services = backing?.services || [];
          const runs = state.byEnv?.[env.id]?.runs?.length || 0;
          return (
            <Stack
              key={env.id} direction="row" alignItems="center" spacing={1.5}
              onClick={() => onOpen(env.id)}
              sx={{
                p: 1.75, borderRadius: 1.5, cursor: "pointer",
                border: "1px solid", borderColor: "divider",
                bgcolor: "background.paper",
                "&:hover": {
                  borderColor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.4 : 0.28),
                },
              }}
            >
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexShrink: 0 }}>
                {services.slice(0, 3).map((sId) => (
                  <TwinLogo key={sId} twin={twinById(sId)} width={20} />
                ))}
                {services.length > 3 && (
                  <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle" }}>
                    +{services.length - 3}
                  </Typography>
                )}
              </Stack>
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s1", fontWeight: 700 }}>{env.name}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                  {services.length} twinned service{services.length === 1 ? "" : "s"} · {runs} run{runs === 1 ? "" : "s"}
                </Typography>
              </Box>
              <Iconify icon="eva:arrow-ios-forward-fill" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            </Stack>
          );
        })}
      </Box>
    </Box>
  );
}
MyTwinEnvsTabView.propTypes = {
  envs: PropTypes.array,
  state: PropTypes.object,
  onOpen: PropTypes.func,
  onGoCatalog: PropTypes.func,
};
