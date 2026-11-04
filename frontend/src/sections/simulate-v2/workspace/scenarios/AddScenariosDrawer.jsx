import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import DatasetImport from "./DatasetImport";
import ScriptUpload from "./ScriptUpload";
import ManualScenarioForm from "./ManualScenarioForm";
import { enqueueSnackbar } from "notistack";
import { useEnvState } from "../../store";
import { useRoleGates } from "../../_mock/previewRole";
import { currentEnvVersion } from "../../_mock/versions";
import { sourceOf } from "../../_mock/scenarioProvenance";
import { validateRows, validatePatch } from "../../_mock/scenarioOrg";
import ErrorFeedImport from "./ErrorFeedImport";
import FailingCasesImport from "./FailingCasesImport";
import ChatSource from "./ChatSource";
import GenerateScenarios from "./GenerateScenarios";
import ProductionReplayImport from "./ProductionReplayImport";
import { injectComposerScaffold } from "../../_mock/composerScaffoldBus";
import { openBuilderChat } from "../../_mock/builderPromptBus";
import {
  productionScenariosOn,
  PRODUCTION_OFF_REASON,
} from "../../_mock/rsiModel";
import { useWorkspaceSettings } from "../../store";
import { focusRing } from "../../components/clickable";

/* The builder chat is the other way to add scenarios — say what's missing and
   it writes them. This is the skill chip that hands the job over. */
const ADD_IN_CHAT = {
  label: "Add scenarios",
  prompt: "Add scenarios",
  icon: "solar:layers-minimalistic-linear",
  placeholder:
    "Describe what's missing — e.g. callers disputing a double charge…",
};

/**
 * Add scenarios.
 *
 * Scenarios already exist by the time anyone reaches this screen — they are
 * derived from the agent when the environment is built. So these routes are
 * not how you get scenarios, they are how you add ones the derivation could
 * not know to write.
 *
 * Every source is shown the same way, in two rows: ways to write new
 * scenarios, and ways to turn what already happened into scenarios.
 *
 * Behind a button, and in a drawer, so the page leads with the scenarios you
 * have and the routes stay one click away.
 */
const GROUPS = [
  {
    label: "Write new",
    routes: [
      {
        id: "generate",
        label: "Generate",
        icon: "solar:magic-stick-3-linear",
        blurb: "More across callers and moods",
      },
      {
        id: "chat",
        label: "From chat",
        icon: "solar:chat-round-line-linear",
        blurb: "Describe it; the builder writes it",
      },
      {
        id: "manual",
        label: "By hand",
        icon: "solar:pen-new-square-linear",
        blurb: "Write one yourself",
      },
      {
        id: "script",
        label: "From a script",
        icon: "solar:document-text-linear",
        blurb: "Call script, SOP or runbook",
      },
    ],
  },
  {
    label: "From what already happened",
    routes: [
      {
        id: "error-feed",
        label: "From Error Feed",
        icon: "solar:bug-linear",
        blurb: "Real users · an issue's failing conversations",
      },
      {
        id: "failing",
        label: "From failing cases",
        icon: "solar:restart-linear",
        blurb: "Simulated · failed or flaky in this environment's runs",
      },
      /* Exact picked evidence becomes reviewed drafts. */
      {
        id: "replay",
        label: "Import evidence",
        icon: "solar:server-linear",
        blurb: "Transcripts or CSV/JSON files you upload",
      },
      {
        id: "dataset",
        label: "From a dataset",
        icon: "solar:database-linear",
        blurb: "Each row becomes a task",
      },
    ],
  },
];

/* `?add=production` predates the Error Feed source — same place now. */
const ROUTE_ALIASES = { production: "error-feed" };

export default function AddScenariosDrawer({
  open,
  onClose,
  env,
  envState,
  selected,
  onAdd,
  onBuilderPrompt,
  initialRoute,
}) {
  const first = ROUTE_ALIASES[initialRoute] || initialRoute || null;
  const [route, setRoute] = useState(first);
  /* Production-derived sources follow the workspace-admin switch in Settings. */
  const { settings: workspaceSettings } = useWorkspaceSettings();
  const productionOff = !productionScenariosOn(envState, workspaceSettings);
  const PRODUCTION_ROUTES = new Set(["error-feed", "replay"]);
  const offRoute = productionOff && PRODUCTION_ROUTES.has(route);

  const close = () => {
    setRoute(null);
    onClose();
  };

  /* Close first, then let the parent show the confirmation toast. */
  const add = (rows) => {
    onAdd(rows, route);
    close();
  };
  /* Re-check only what's still blocked, under the same import. */
  const { patch } = useEnvState(env?.id);
  const gates = useRoleGates();
  const blocked = (envState?.scenarios || []).filter((r) => r.blocked);
  const retryBlocked =
    blocked.length && gates.canEdit
      ? () => {
          const res = validateRows(
            blocked,
            env,
            currentEnvVersion(env, envState).label,
            envState,
          );
          patch(validatePatch(envState, res));
          const ok = res.filter((x) => x.result.pass).length;
          enqueueSnackbar(
            `${ok} of ${res.length} now pass and are ready to add; ${res.length - ok} still blocked.`,
            { variant: ok ? "success" : "info" },
          );
        }
      : null;

  const toggle = (id) => setRoute((r) => (r === id ? null : id));

  /* Close, then hand over once the drawer is gone — a closing drawer hands
     focus back to the button that opened it, which would pull the cursor
     straight out of the chat. */
  const openChat = () => {
    close();
    setTimeout(() => injectComposerScaffold(ADD_IN_CHAT), 260);
  };
  const sendToChat = (ask) => {
    if (!onBuilderPrompt) {
      openChat();
      return;
    }
    close();
    setTimeout(() => {
      openBuilderChat();
      onBuilderPrompt(`Add scenarios. ${ask}`);
    }, 260);
  };

  return (
    <SideDrawer open={open} onClose={close} width={960}>
      <Stack sx={{ height: "100%" }}>
        {/*
          Header stays constant — no title-swap when a route is picked.
          The card grid is always visible below it, so the reader always
          knows which route they're on (the highlighted card) and can
          switch routes without a back button.
        */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Add scenarios
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              This environment already has its scenarios. Add the ones it
              couldn&apos;t work out from your agent on its own.
            </Typography>
          </Box>
          <IconButton size="small" onClick={close}>
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        {/*
          Body is a vertical flex column now. The card grid stays at its
          natural height at the top; the picker below it takes flex: 1 so
          its section-card frame extends all the way to the bottom of the
          drawer instead of stopping after the last table row.
        */}
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            p: 2.5,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/*
            Cards always render; the active one shows the same border
            and tint as the coverage-depth picks below. When one is picked, the picker renders directly
            below the grid rather than replacing it, so switching to a
            different route is one click, not click-back-click.
          */}
          <Stack spacing={1.5} sx={{ flexShrink: 0, mb: route ? 2.5 : 0 }}>
            {GROUPS.map((g) => (
              <Box key={g.label}>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontWeight: 500,
                    mb: 0.75,
                  }}
                >
                  {g.label}
                </Typography>
                <Box
                  sx={{
                    display: "grid",
                    gap: 1,
                    gridTemplateColumns: {
                      xs: "repeat(2, minmax(0, 1fr))",
                      md: "repeat(4, minmax(0, 1fr))",
                    },
                  }}
                >
                  {g.routes.map((r) => {
                    const off = productionOff && PRODUCTION_ROUTES.has(r.id);
                    return (
                      <SourceTile
                        key={r.id}
                        route={r}
                        active={route === r.id}
                        off={off}
                        onClick={() => toggle(r.id)}
                      />
                    );
                  })}
                </Box>
              </Box>
            ))}
          </Stack>

          {/* picker fills the remaining vertical space so the section-card
              frame reaches the bottom of the drawer instead of clipping
              right after the last row */}
          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            {offRoute && (
              <Stack
                alignItems="center"
                spacing={1}
                sx={{
                  py: 6,
                  px: 3,
                  textAlign: "center",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                }}
              >
                <Iconify
                  icon="solar:server-linear"
                  width={26}
                  sx={{ color: "text.disabled" }}
                />
                <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                  Scenarios from production are off
                </Typography>
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.tertiary",
                    maxWidth: 520,
                  }}
                >
                  {PRODUCTION_OFF_REASON}
                </Typography>
              </Stack>
            )}
            {route === "generate" && (
              <GenerateScenarios
                env={env}
                envState={envState}
                onAdd={add}
                selected={selected}
              />
            )}
            {route === "error-feed" && !offRoute && (
              <ErrorFeedImport
                env={env}
                envState={envState}
                onAdd={add}
                selected={selected}
                onClose={close}
              />
            )}
            {route === "replay" && !offRoute && (
              <ProductionReplayImport
                env={env}
                envState={envState}
                onAdd={add}
                selected={selected}
              />
            )}
            {route === "failing" && (
              <FailingCasesImport
                env={env}
                envState={envState}
                onAdd={add}
                selected={selected}
              />
            )}
            {route === "chat" && (
              <ChatSource onSend={sendToChat} onOpenChat={openChat} />
            )}
            {route === "dataset" && (
              <DatasetImport env={env} onAdd={add} selected={selected} />
            )}
            {route === "script" && (
              <ScriptUpload env={env} onAdd={add} selected={selected} />
            )}
            {route === "manual" && <ManualScenarioForm env={env} onAdd={add} />}
          </Box>
          {/* Every import's counts stay — created, blocked, duplicate — and
              Retry re-checks only the blocked ones. */}
          {!route && (envState?.importHistory || []).length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  mb: 0.5,
                }}
              >
                Recent imports
              </Typography>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {[...envState.importHistory]
                  .reverse()
                  .slice(0, 5)
                  .map((h) => (
                    <Stack
                      key={h.at}
                      direction="row"
                      spacing={1.5}
                      alignItems="center"
                      sx={{
                        px: 1.25,
                        py: 0.75,
                        "& + &": {
                          borderTop: "1px solid",
                          borderColor: "divider",
                        },
                      }}
                    >
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          width: 92,
                          flexShrink: 0,
                        }}
                      >
                        {new Date(h.at).toLocaleString("en-GB", {
                          day: "numeric",
                          month: "short",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </Typography>
                      <Typography sx={{ typography: "s3", flex: 1 }}>
                        {`${sourceOf(h.source).label} · ${h.created} created · ${h.blocked} blocked · ${h.duplicate} duplicate`}
                      </Typography>
                      {h.blocked > 0 && retryBlocked && (
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={retryBlocked}
                          sx={{ minWidth: 0 }}
                        >
                          Retry blocked
                        </Button>
                      )}
                    </Stack>
                  ))}
              </Box>
            </Box>
          )}
        </Box>

        {!route && (
          <Stack
            direction="row"
            justifyContent="flex-end"
            sx={{
              px: 2.5,
              py: 1.75,
              borderTop: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Button
              size="small"
              onClick={close}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

AddScenariosDrawer.propTypes = {
  initialRoute: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  selected: PropTypes.array,
  onAdd: PropTypes.func,
  onBuilderPrompt: PropTypes.func,
};

/* The coverage-depth picks' selected tint, laid over a surface as an image so
   it tints the thumb strip and the text area alike without flattening them
   into one colour — the card keeps the same two-part shape selected or not. */
const selectedTint = (t) => {
  const c = t.palette.status.discovery.bg;
  return `linear-gradient(${c}, ${c})`;
};

/* One source: icon, name and one line — every source the same shape. */
function SourceTile({ route, active, off = false, onClick }) {
  return (
    <ButtonBase
      type="button"
      title={off ? PRODUCTION_OFF_REASON : undefined}
      onClick={onClick}
      aria-pressed={active}
      sx={{
        ...focusRing,
        ...(off && { opacity: 0.6 }),
        display: "flex",
        width: 1,
        minWidth: 0,
        alignItems: "flex-start",
        justifyContent: "flex-start",
        gap: 1,
        px: 1.25,
        py: 1,
        textAlign: "left",
        font: "inherit",
        borderRadius: "8px",
        border: "1px solid",
        borderColor: active ? "text.primary" : "divider",
        bgcolor: "background.paper",
        backgroundImage: active ? selectedTint : "none",
        transition: "border-color .16s ease",
        "&:hover": { borderColor: active ? "text.primary" : "text.tertiary" },
      }}
    >
      <Iconify
        icon={route.icon}
        width={16}
        sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
      />
      <Box minWidth={0}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}
        >
          {route.label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {off ? "Off in Settings" : route.blurb}
        </Typography>
      </Box>
    </ButtonBase>
  );
}
SourceTile.propTypes = {
  route: PropTypes.object,
  active: PropTypes.bool,
  off: PropTypes.bool,
  onClick: PropTypes.func,
};
