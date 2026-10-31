import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { injectComposerScaffold } from "src/sections/simulate/environments/buildEnvironment/console/composerScaffoldBus";
import SideDrawer from "../../components/SideDrawer";
import {
  ChatThumb,
  FailingCasesThumb,
  GenerateThumb,
  ProductionThumb,
} from "./add/RouteThumbs";
import GenerateRoute from "./add/GenerateRoute";
import ChatRoute from "./add/ChatRoute";
import ErrorFeedRoute from "./add/ErrorFeedRoute";
import FailingCasesRoute from "./add/FailingCasesRoute";
import ProductionRoute from "./add/ProductionRoute";
import DatasetRoute from "./add/DatasetRoute";
import ScriptRoute from "./add/ScriptRoute";

const ROUTES = [
  {
    id: "generate",
    label: "Generate",
    blurb: "New scenarios across tasks, callers, moods and lines.",
    Thumb: GenerateThumb,
  },
  {
    id: "error-feed",
    label: "From Error Feed",
    blurb: "Turn an issue's failing traces into scenarios.",
    Thumb: ProductionThumb,
  },
  {
    id: "failing",
    label: "From failing cases",
    blurb: "Re-test what failed in past runs.",
    Thumb: FailingCasesThumb,
  },
  {
    id: "chat",
    label: "From chat",
    blurb: "Say what's missing and the builder chat writes it.",
    Thumb: ChatThumb,
  },
];

// Kept, but quieter: one line each under More sources.
const MORE_ROUTES = [
  {
    id: "replay",
    label: "Production, word for word",
    icon: "solar:server-linear",
    hint: "Replay a production conversation's caller turns exactly as they were said",
  },
  {
    id: "dataset",
    label: "A dataset",
    icon: "solar:database-linear",
    hint: "Turn a dataset's rows into tasks",
  },
  {
    id: "script",
    label: "A script",
    icon: "solar:document-text-linear",
    hint: "Pull scenarios out of a call script, SOP or runbook",
  },
];

const selectedTint = (t) =>
  `linear-gradient(${alpha(t.palette.primary.main, 0.06)}, ${alpha(t.palette.primary.main, 0.06)})`;

// Vel's Add scenarios. Scenarios already exist once the environment is built,
// so these routes add the ones the derivation couldn't know to write. Four
// sources lead; production replay, a dataset and a script sit behind More
// sources. Generate is real (the extend API); the others say what they send.
export default function AddScenariosDrawer({
  open,
  onClose,
  env,
  envState,
  initialRoute = null,
}) {
  const [route, setRoute] = useState(initialRoute);
  const [moreOpen, setMoreOpen] = useState(() =>
    MORE_ROUTES.some((r) => r.id === initialRoute),
  );
  const close = () => {
    setRoute(null);
    onClose();
  };
  const toggle = (id) => setRoute((r) => (r === id ? null : id));
  // Pin the ask in the builder chat; the chat opens itself for a new pin.
  const sendToChat = (ask) => {
    close();
    injectComposerScaffold(ask ? `Add scenarios. ${ask}` : "Add scenarios");
  };
  const routeProps = { env, envState, onClose: close };

  return (
    <SideDrawer open={open} onClose={close} width={960} hideClose>
      <Stack sx={{ height: "100%" }}>
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
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Add scenarios
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              This environment already has its scenarios. Add the ones it
              couldn&apos;t work out from your agent on its own.
            </Typography>
          </Box>
          <IconButton size="small" onClick={close} aria-label="Close">
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.subtitle" }}
            />
          </IconButton>
        </Stack>

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
          <Box
            sx={{
              display: "grid",
              gap: 1.5,
              gridTemplateColumns: {
                xs: "repeat(2, 1fr)",
                md: `repeat(${ROUTES.length}, 1fr)`,
              },
              flexShrink: 0,
            }}
          >
            {ROUTES.map((r) => (
              <RouteCard
                key={r.id}
                route={r}
                active={route === r.id}
                onClick={() => toggle(r.id)}
              />
            ))}
          </Box>

          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mt: 1.25, mb: route ? 2.5 : 0, flexShrink: 0, minHeight: 30 }}
          >
            <Button
              size="small"
              onClick={() => setMoreOpen((v) => !v)}
              endIcon={
                <Iconify
                  icon={
                    moreOpen
                      ? "solar:alt-arrow-left-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={12}
                />
              }
              sx={{ color: "text.secondary", ml: -0.75, flexShrink: 0 }}
            >
              More sources
            </Button>
            <Collapse in={moreOpen} orientation="horizontal" unmountOnExit>
              <Stack direction="row" spacing={0.75}>
                {MORE_ROUTES.map((r) => (
                  <MoreSource
                    key={r.id}
                    route={r}
                    active={route === r.id}
                    onClick={() => toggle(r.id)}
                  />
                ))}
              </Stack>
            </Collapse>
          </Stack>

          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            {route === "generate" && <GenerateRoute {...routeProps} />}
            {route === "error-feed" && <ErrorFeedRoute {...routeProps} />}
            {route === "failing" && <FailingCasesRoute {...routeProps} />}
            {route === "chat" && <ChatRoute onSend={sendToChat} />}
            {route === "replay" && <ProductionRoute {...routeProps} />}
            {route === "dataset" && <DatasetRoute {...routeProps} />}
            {route === "script" && <ScriptRoute {...routeProps} />}
          </Box>
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
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.shape({ id: PropTypes.string, surface: PropTypes.string }),
  envState: PropTypes.object,
  initialRoute: PropTypes.string,
};

function RouteCard({ route, active, onClick }) {
  const { Thumb } = route;
  return (
    <Box
      role="button"
      tabIndex={0}
      aria-pressed={active}
      aria-label={route.label}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        borderRadius: "8px",
        overflow: "hidden",
        height: "100%",
        cursor: "pointer",
        border: "1px solid",
        borderColor: active ? "text.primary" : "divider",
        bgcolor: "background.paper",
        backgroundImage: active ? selectedTint : "none",
        transition: "border-color .16s ease",
        "&:hover": { borderColor: active ? "text.primary" : "text.disabled" },
      }}
    >
      <Box
        sx={{
          height: 72,
          display: "grid",
          placeItems: "center",
          overflow: "hidden",
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
          "& > svg": {
            transform: "scale(0.68)",
            transformOrigin: "center center",
          },
        }}
      >
        <Thumb />
      </Box>
      <Box sx={{ px: 1.5, py: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {route.label}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {route.blurb}
        </Typography>
      </Box>
    </Box>
  );
}
RouteCard.propTypes = {
  route: PropTypes.object.isRequired,
  active: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
};

function MoreSource({ route, active, onClick }) {
  return (
    <ButtonBase
      title={route.hint}
      onClick={onClick}
      sx={{
        gap: 0.75,
        height: 30,
        px: 1.25,
        whiteSpace: "nowrap",
        borderRadius: 1,
        border: "1px solid",
        borderColor: active ? "text.primary" : "divider",
        bgcolor: "background.paper",
        backgroundImage: active ? selectedTint : "none",
        color: active ? "text.primary" : "text.secondary",
        "&:hover": {
          borderColor: active ? "text.primary" : "text.disabled",
          color: "text.primary",
        },
      }}
    >
      <Iconify icon={route.icon} width={14} />
      <Typography
        component="span"
        sx={{ typography: "s2", fontWeight: 600, color: "inherit" }}
      >
        {route.label}
      </Typography>
    </ButtonBase>
  );
}
MoreSource.propTypes = {
  route: PropTypes.object.isRequired,
  active: PropTypes.bool,
  onClick: PropTypes.func.isRequired,
};
