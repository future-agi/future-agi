import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, IconButton, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import TemplatePicker from "./TemplatePicker";
import DatasetImport from "./DatasetImport";
import ScriptUpload from "./ScriptUpload";
import ProductionImport from "./ProductionImport";
import TwinScenarioPicker from "./TwinScenarioPicker";
import { PackThumb, DatasetThumb, ScriptThumb, ProductionThumb } from "./RouteThumbs";

/**
 * Add scenarios.
 *
 * Scenarios already exist by the time anyone reaches this screen — they are
 * derived from the agent when the environment is built. So these routes are
 * not how you get scenarios, they are how you add ones the derivation could
 * not know to write. Five cards across the top of the page said the opposite:
 * that nothing had happened yet and a route had to be chosen.
 *
 * Behind a button, and in a drawer, so the page leads with the scenarios you
 * have and the routes stay one click away.
 */
/**
 * Small purple twin thumb so the "From twin services" card visually
 * belongs with the twin surface everywhere else (env creation flow,
 * timeline, gallery chip). Kept inline — the thumb is one glyph in a
 * tinted square, not enough to justify its own file next to the
 * multi-shape SVG thumbs in RouteThumbs.
 */
function TwinThumb() {
  return (
    <Box
      sx={{
        width: 96, height: 60, borderRadius: 1.25,
        display: "grid", placeItems: "center",
        bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.08),
      }}
    >
      <Iconify icon="solar:server-square-bold" width={28} sx={{ color: "#7857FC" }} />
    </Box>
  );
}

const ROUTES = [
  {
    id: "templates",
    label: "Scenario pack",
    blurb: "Curated packs built for this environment — happy paths, edge cases and adversarial probes.",
    Thumb: PackThumb,
  },
  {
    id: "twin",
    label: "From twin services",
    blurb: "Cross-service scenarios generated from your env's twin backing — Slack → Notion, Gmail → Salesforce, and more.",
    Thumb: TwinThumb,
  },
  {
    id: "production",
    label: "From production",
    blurb: "Promote failure clusters from the Error Feed. Every real regression becomes a permanent test.",
    Thumb: ProductionThumb,
  },
  {
    id: "dataset",
    label: "From a dataset",
    blurb: "Choose a dataset and the columns that matter, and turn its rows into tasks.",
    Thumb: DatasetThumb,
  },
  {
    id: "script",
    label: "Upload a script",
    blurb: "Drop in a call script, SOP or runbook and we pull out the scenarios it describes.",
    Thumb: ScriptThumb,
  },
];

export default function AddScenariosDrawer({ open, onClose, env, envState, selected, onAdd }) {
  const [route, setRoute] = useState(null);
  /*
    Route visibility depends on the env's shape:
      · templates route hides for user-agent-built envs (their scenarios
        are derived from the source; a pack would overlap).
      · twin route only shows when the env has a twin backing — the
        picker's suggestions are generated from the twin services and
        make no sense otherwise.
  */
  const routes = ROUTES.filter((r) => {
    if (r.id === "templates" && env?.builtFrom) return false;
    if (r.id === "twin" && !envState?.twinBacking) return false;
    return true;
  });

  const close = () => { setRoute(null); onClose(); };

  /* Close first, then let the parent show the confirmation toast. */
  const add = (rows) => { onAdd(rows, route); close(); };

  const toggle = (id) => setRoute((r) => (r === id ? null : id));

  return (
    <SideDrawer open={open} onClose={close} width={1120}>
      <Stack sx={{ height: "100%" }}>
        {/*
          Header stays constant — no title-swap when a route is picked.
          The card grid is always visible below it, so the reader always
          knows which route they're on (the highlighted card) and can
          switch routes without a back button.
        */}
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Add scenarios</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Your agent&apos;s scenarios are already here. These add the ones reading it could not know to write.
            </Typography>
          </Box>
          <IconButton size="small" onClick={close}>
            <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        {/*
          Body is a vertical flex column now. The card grid stays at its
          natural height at the top; the picker below it takes flex: 1 so
          its section-card frame extends all the way to the bottom of the
          drawer instead of stopping after the last table row.
        */}
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5, display: "flex", flexDirection: "column" }}>
          {/*
            Cards always render; the active one shows a purple border
            and tint. When one is picked, the picker renders directly
            below the grid rather than replacing it, so switching to a
            different route is one click, not click-back-click.
          */}
          <Box
            sx={{
              display: "grid",
              gap: 1.5,
              gridTemplateColumns: `repeat(${routes.length}, 1fr)`,
              mb: route ? 2.5 : 0,
              flexShrink: 0,
            }}
          >
            {routes.map((r) => (
              <RouteCard
                key={r.id}
                route={r}
                active={route === r.id}
                onClick={() => toggle(r.id)}
              />
            ))}
          </Box>

          {/* picker fills the remaining vertical space so the section-card
              frame reaches the bottom of the drawer instead of clipping
              right after the last row */}
          <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {route === "templates" && <TemplatePicker env={env} onAdd={add} selected={selected} />}
            {route === "twin" && <TwinScenarioPicker env={env} envState={envState} onAdd={add} selected={selected} />}
            {route === "production" && <ProductionImport env={env} onAdd={add} selected={selected} />}
            {route === "dataset" && <DatasetImport env={env} onAdd={add} selected={selected} />}
            {route === "script" && <ScriptUpload env={env} onAdd={add} selected={selected} />}
          </Box>
        </Box>

        {!route && (
          <Stack
            direction="row" justifyContent="flex-end"
            sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
          >
            <Button onClick={close} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
              Cancel
            </Button>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

AddScenariosDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  selected: PropTypes.array,
  onAdd: PropTypes.func,
};

function RouteCard({ route, active, onClick }) {
  const { Thumb } = route;
  return (
    <Box
      onClick={onClick}
      sx={{
        borderRadius: 1.5, overflow: "hidden", height: "100%", cursor: "pointer",
        border: "1px solid",
        borderColor: active ? "#7857FC" : "divider",
        bgcolor: "background.paper",
        transition: "border-color .16s ease, background-color .16s ease",
        boxShadow: active ? (t) => `0 0 0 1px ${t.palette.mode === "dark" ? "#7857FC55" : "#7857FC33"}` : "none",
        "&:hover": { borderColor: active ? "#7857FC" : "text.subtitle" },
      }}
    >
      {/*
        Thumb area shrunk and the SVG scaled 0.68× — the original 108px
        strip with a full-size illustration ate a third of the drawer
        every time the picker rendered below. Cards read at a glance
        without the extra whitespace.
      */}
      <Box
        sx={{
          height: 72, display: "grid", placeItems: "center", overflow: "hidden",
          bgcolor: active
            ? (t) => (t.palette.mode === "dark" ? "rgba(120,87,252,0.09)" : "rgba(120,87,252,0.05)")
            : "background.neutral",
          borderBottom: "1px solid", borderColor: "divider",
          "& > svg": { transform: "scale(0.68)", transformOrigin: "center center" },
        }}
      >
        <Thumb />
      </Box>
      <Box sx={{ px: 1.5, py: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
          {route.label}
        </Typography>
        <Typography
          sx={{
            typography: "s3", color: "text.subtitle",
            display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {route.blurb}
        </Typography>
      </Box>
    </Box>
  );
}
RouteCard.propTypes = { route: PropTypes.object, active: PropTypes.bool, onClick: PropTypes.func };
