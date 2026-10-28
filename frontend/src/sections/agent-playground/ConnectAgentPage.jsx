import React, { useLayoutEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { pageBackButtonSx } from "src/sections/settings/integrations/styles";
import ConnectSection from "src/sections/simulate/environments/buildDrawer/ConnectSection";
import LogoTile from "src/sections/simulate/environments/buildDrawer/LogoTile";
import { FORMS_BY_PANEL } from "src/sections/simulate/environments/buildDrawer/forms/formsByPanel";
import {
  LINK_SX,
  T12,
  T14,
} from "src/sections/simulate/environments/buildDrawer/drawerStyles";
import UiOnlyFlag from "src/sections/simulate/environments/components/UiOnlyFlag";
import useCanEditAgent from "./hooks/useCanEditAgent";
import useCreatePlaygroundAgent from "./hooks/useCreatePlaygroundAgent";
import { CONNECTED_AGENTS_PATH } from "./agentsTabs";

function BuildOneLine() {
  const { create, isPending } = useCreatePlaygroundAgent();
  return (
    <Typography sx={{ ...T12, color: "text.secondary", flexShrink: 0 }}>
      {"No agent yet? "}
      <ButtonBase onClick={create} disabled={isPending} sx={LINK_SX}>
        Build one in Future AGI
      </ButtonBase>
      {". Write its prompt, pick a model, add tools."}
    </Typography>
  );
}

/**
 * New agent: every way to connect an agent that already runs, as the Build
 * environment drawer's Connect your agent lists them. Picking one opens its
 * setup form in the page; the list stays mounted underneath, so going back
 * finds the same tab and search. While a form is creating its build, nothing
 * leaves it, so its hand-off to the new environment always runs.
 */
export default function ConnectAgentPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { canCreate } = useCanEditAgent();
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);
  const listScroll = useRef(0);
  const backRef = useRef(null);

  // The form opens at its top with focus on its way back; the list comes
  // back where it was left.
  useLayoutEffect(() => {
    const box = scrollRef.current;
    if (picked) {
      if (box) box.scrollTop = 0;
      backRef.current?.focus({ preventScroll: true });
    } else if (box) {
      box.scrollTop = listScroll.current;
    }
  }, [picked]);

  const leave = () => {
    if (busy) return;
    if (location.key !== "default") navigate(-1);
    else navigate(CONNECTED_AGENTS_PATH);
  };
  const pick = (item) => {
    listScroll.current = scrollRef.current?.scrollTop ?? 0;
    setPicked(item);
  };
  const back = () => {
    if (!busy) setPicked(null);
  };

  const Form = picked ? FORMS_BY_PANEL[picked.setup.panel] : null;

  return (
    <Box
      ref={scrollRef}
      sx={{ height: "100%", overflow: "auto", px: 2, pt: 2, pb: 4 }}
    >
      <Helmet>
        <title>New agent</title>
      </Helmet>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1}
        sx={{ mb: 2.5 }}
      >
        <IconButton
          size="small"
          aria-label="Back"
          onClick={leave}
          disabled={busy}
          sx={{ mt: 0.25 }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={20} />
        </IconButton>
        <Box>
          <Typography
            component="h1"
            typography="m2"
            fontWeight="fontWeightMedium"
          >
            New agent
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Connect it from where it runs today, or build one here.
          </Typography>
        </Box>
      </Stack>

      <Box hidden={!!picked}>
        <ConnectSection
          head={false}
          onPick={pick}
          aside={canCreate ? <BuildOneLine /> : null}
        />
      </Box>

      {picked && (
        <Box>
          <Button
            ref={backRef}
            variant="outlined"
            startIcon={<Iconify icon="formkit:left" width={16} height={16} />}
            onClick={back}
            disabled={busy}
            sx={{ mb: 2, ...pageBackButtonSx }}
          >
            Back to New agent
          </Button>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ mb: 2 }}
          >
            {(picked.logo || picked.icon) && (
              <LogoTile logo={picked.logo} icon={picked.icon} size={36} />
            )}
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography
                  component="h2"
                  noWrap
                  sx={{ ...T14, fontWeight: 600, minWidth: 0 }}
                >
                  {picked.setup.title || picked.name}
                </Typography>
                {picked.setup.uiOnly && <UiOnlyFlag />}
              </Stack>
              {picked.setup.subtitle && (
                <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
                  {picked.setup.subtitle}
                </Typography>
              )}
            </Box>
          </Stack>
          <Form item={picked} onBusyChange={setBusy} />
        </Box>
      )}
    </Box>
  );
}
