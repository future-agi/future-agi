import PropTypes from "prop-types";
import {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { enqueueSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Slide,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import DrawerCatalog from "./DrawerCatalog";
import ConnectSection from "./ConnectSection";
import { connectItemsFor } from "./buildDrawerCatalog";
import {
  ConnectModeContext,
  ConnectNewAgentContext,
  connectSubtitleFor,
  connectTitleFor,
} from "./connectNewAgent";
import LogoTile from "./LogoTile";
import UiOnlyFlag from "../components/UiOnlyFlag";
import { BTN_SX, T12, T14 } from "./drawerStyles";
import { FORMS_BY_PANEL } from "./forms/formsByPanel";
import { QuietTag } from "./starts/startBits";
import { SCRATCH_ITEM } from "./starts/startItems";

const DRAWER_WIDTH = 960;

const CloseButton = forwardRef(function CloseButton(
  { label, onClick, disabled, icon = "solar:close-linear" },
  ref,
) {
  return (
    <IconButton
      ref={ref}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      size="small"
      sx={{ color: "text.secondary", flexShrink: 0 }}
    >
      <Iconify icon={icon} width={18} />
    </IconButton>
  );
});
CloseButton.propTypes = {
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  icon: PropTypes.string,
};

const headerSx = {
  borderBottom: "1px solid",
  borderColor: "divider",
  flexShrink: 0,
};

const titleOf = (layer) =>
  layer.kind === "connect"
    ? connectTitleFor(layer.modality)
    : layer.item.setup.title || layer.item.name;

/**
 * the design's Build environment drawer: every way in, and the picked one's setup in
 * the same drawer, one drawer at a time. Every prebuilt world is a view of its
 * own. What opens over the list is a stack of layers: a setup, and from an
 * agent picker in it, the ways to connect a new agent and then that way's
 * form. Every layer stays mounted under the one above, so going back finds it
 * as it was. Each layer slides in over the one below, as the design's nested drawers
 * do. The back arrow (and Esc) slides it back out; the X closes the whole
 * drawer. While a build is being created nothing closes, so its hand-off
 * to the environment page always runs.
 */
export default function BuildEnvironmentDrawer({ open, onClose }) {
  // { kind: "setup", item, at, connect? } or { kind: "connect", modality,
  // onConnected, at }. `at` keys a layer, so opening a way again starts fresh.
  const [layers, setLayers] = useState([]);
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  // Agents connected from here, which the agent pickers list first.
  const [samples, setSamples] = useState([]);
  const nextAt = useRef(0);
  const backRef = useRef(null);
  const scrollBoxes = useRef(new Map());
  const depth = useRef(0);
  const stageRef = useRef(null);
  const theme = useTheme();
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");

  const push = useCallback((layer) => {
    nextAt.current += 1;
    const at = nextAt.current;
    setLayers((ls) => [...ls, { ...layer, at }]);
  }, []);
  const pick = (it) => push({ kind: "setup", item: it });
  const requestConnect = useCallback(
    ({ modality = null, onConnected }) =>
      push({ kind: "connect", modality, onConnected }),
    [push],
  );
  const connectApi = useMemo(
    () => ({ requestConnect, samples }),
    [requestConnect, samples],
  );

  // The connect layer and the form over it give way to the setup that asked.
  const finishConnect = (connectLayer, agent) => {
    setLayers((ls) =>
      ls.map((l) => (l.at >= connectLayer.at ? { ...l, leaving: true } : l)),
    );
    setSamples((s) => [agent, ...s]);
    enqueueSnackbar(
      `UI only: ${agent.name} was added as a sample agent for this setup.`,
      { variant: "info" },
    );
    connectLayer.onConnected?.(agent);
  };
  const pickToConnect = (connectLayer) => (it) =>
    push({
      kind: "setup",
      item: it,
      connect: {
        item: it,
        modality: connectLayer.modality,
        onConnected: (agent) => finishConnect(connectLayer, agent),
      },
    });

  // A layer going back stays until it has slid out.
  const live = layers.filter((l) => !l.leaving);
  const top = live[live.length - 1] || null;

  // A layer opens at its top, with focus on its way back.
  const topAt = top?.at;
  const count = live.length;
  useEffect(() => {
    const opened = count > depth.current;
    depth.current = count;
    if (topAt == null) return;
    const box = scrollBoxes.current.get(topAt);
    if (opened && box) box.scrollTop = 0;
    backRef.current?.focus({ preventScroll: true });
  }, [topAt, count]);

  const finish = () => {
    setLayers([]);
    setSamples([]);
    onClose();
  };
  const back = () => {
    if (busy || !top) return;
    setLayers((ls) =>
      ls.map((l) => (l.at === top.at ? { ...l, leaving: true } : l)),
    );
  };
  const removeLayer = (at) =>
    setLayers((ls) => ls.filter((l) => !(l.leaving && l.at === at)));
  const close = () => {
    if (busy) return;
    if (top) {
      back();
      return;
    }
    finish();
  };
  const closeAll = () => {
    if (!busy) finish();
  };

  const layerHeader = (layer) => {
    const index = live.indexOf(layer);
    const under = index > 0 ? live[index - 1] : null;
    const isTop = layer === top;
    const backLabel = under
      ? `Back to ${titleOf(under)}`
      : "Back to build environment";
    const item = layer.kind === "setup" ? layer.item : null;
    const setup = item?.setup;
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ ...headerSx, px: 2.5, py: 1.75 }}
      >
        <CloseButton
          ref={isTop ? backRef : undefined}
          label={backLabel}
          icon="solar:alt-arrow-left-linear"
          onClick={back}
          disabled={busy}
        />
        {item && (item.logo || item.icon) && (
          <LogoTile logo={item.logo} icon={item.icon} size={36} />
        )}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
            <Typography noWrap sx={{ ...T14, fontWeight: 600, minWidth: 0 }}>
              {titleOf(layer)}
            </Typography>
            {item?.tag && <QuietTag>{item.tag}</QuietTag>}
            {(setup?.uiOnly || layer.connect) && <UiOnlyFlag />}
          </Stack>
          {item ? (
            setup.subtitle && (
              <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
                {setup.subtitle}
              </Typography>
            )
          ) : (
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              {connectSubtitleFor(layer.modality)}
            </Typography>
          )}
        </Box>
        <CloseButton label="Close" onClick={closeAll} disabled={busy} />
      </Stack>
    );
  };

  const header = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ ...headerSx, px: 3, py: 2 }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ ...T14, fontWeight: 600 }}>
          Build environment
        </Typography>
        <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
          Connect an agent or design an environment from scratch.
        </Typography>
      </Box>
      <Button
        size="small"
        variant="contained"
        color="primary"
        onClick={() => pick(SCRATCH_ITEM)}
        startIcon={<Iconify icon="solar:document-add-linear" width={14} />}
        sx={{ ...BTN_SX, flexShrink: 0 }}
      >
        Build from scratch
      </Button>
      <CloseButton label="Close" onClick={closeAll} />
    </Stack>
  );

  const list = (
    <DrawerCatalog
      onPick={pick}
      onBrowseWorlds={() => {
        closeAll();
        navigate(paths.dashboard.simulate.environments.templates);
      }}
    />
  );
  const scrollSx = { flex: 1, minHeight: 0, overflow: "auto" };

  const layerBody = (layer) => {
    if (layer.kind === "connect") {
      return (
        <Box sx={{ pb: 4 }}>
          <ConnectSection
            items={connectItemsFor(layer.modality)}
            head={false}
            onPick={pickToConnect(layer)}
          />
        </Box>
      );
    }
    if (layer.item.setup.uiOnly || layer.connect) {
      return <Alert severity="info">This setup is unavailable.</Alert>;
    }
    const Form = FORMS_BY_PANEL[layer.item.setup.panel];
    const form = <Form item={layer.item} onBusyChange={setBusy} />;
    return layer.connect ? (
      <ConnectModeContext.Provider value={layer.connect}>
        {form}
      </ConnectModeContext.Provider>
    ) : (
      form
    );
  };
  const layerPad = (layer) =>
    layer.kind === "connect"
      ? { px: 3, pt: 2.5 }
      : // No bottom padding: the form's sticky footer has to meet the
        // drawer's edge, or the fields scroll into view under it.
        layer.item.setup.flush
        ? { p: 0 }
        : { px: 2, pt: 2 };

  const slideMs = reduceMotion
    ? 0
    : {
        enter: theme.transitions.duration.enteringScreen,
        exit: theme.transitions.duration.leavingScreen,
      };

  return (
    <SideDrawer open={open} onClose={close} width={DRAWER_WIDTH} hideClose>
      <ConnectNewAgentContext.Provider value={connectApi}>
        <Box
          ref={stageRef}
          sx={{
            position: "relative",
            height: "100%",
            overflow: "hidden",
          }}
        >
          <Box
            aria-hidden={!!top || undefined}
            inert={top ? "" : undefined}
            sx={{
              height: "100%",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
            }}
          >
            {header}
            <Box sx={{ ...scrollSx, px: 3, pt: 2.5 }}>{list}</Box>
          </Box>
          {layers.map((layer) => (
            <Slide
              key={layer.at}
              direction="left"
              in={!layer.leaving}
              container={stageRef.current}
              timeout={slideMs}
              onExited={() => removeLayer(layer.at)}
            >
              <Box
                aria-hidden={layer !== top || undefined}
                inert={layer !== top ? "" : undefined}
                sx={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  minHeight: 0,
                  bgcolor: "background.paper",
                  boxShadow: (t) =>
                    `-10px 0 40px ${alpha(t.palette.common.black, 0.18)}`,
                }}
              >
                {layerHeader(layer)}
                <Box
                  ref={(el) => {
                    if (el) scrollBoxes.current.set(layer.at, el);
                    else scrollBoxes.current.delete(layer.at);
                  }}
                  sx={{ ...scrollSx, ...layerPad(layer) }}
                >
                  {layerBody(layer)}
                </Box>
              </Box>
            </Slide>
          ))}
        </Box>
      </ConnectNewAgentContext.Provider>
    </SideDrawer>
  );
}
BuildEnvironmentDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
