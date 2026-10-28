import React, { useLayoutEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation, useNavigate } from "react-router-dom";
import { Box, ButtonBase, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ConnectSection from "src/sections/simulate/environments/buildDrawer/ConnectSection";
import LogoTile from "src/sections/simulate/environments/buildDrawer/LogoTile";
import { FORMS_BY_PANEL } from "src/sections/simulate/environments/buildDrawer/forms/formsByPanel";
import {
  LINK_SX,
  T12,
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
    // The padding sits inside the scroller: a sticky form footer measures
    // from the scroller's padding edge, so padding here would lift it off
    // the bottom and show the form beneath it.
    <Box ref={scrollRef} sx={{ height: "100%", overflow: "auto" }}>
      <Helmet>
        <title>New agent</title>
      </Helmet>
      <Box sx={{ px: 2, pt: 2, pb: 4 }}>
        {/* One header and one way back: the list leaves the page, a picked
            way returns to the list. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ mb: 2.5 }}
        >
          <IconButton
            ref={backRef}
            size="small"
            aria-label={picked ? "Back to New agent" : "Back"}
            onClick={picked ? back : leave}
            disabled={busy}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={20} />
          </IconButton>
          {picked && (picked.logo || picked.icon) && (
            <LogoTile logo={picked.logo} icon={picked.icon} size={36} />
          )}
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                component="h1"
                noWrap
                typography="m2"
                fontWeight="fontWeightMedium"
                sx={{ minWidth: 0 }}
              >
                {picked ? picked.setup.title || picked.name : "New agent"}
              </Typography>
              {picked?.setup.uiOnly && <UiOnlyFlag />}
            </Stack>
            <Typography
              noWrap={!!picked}
              sx={{ typography: "s1", color: "text.secondary" }}
            >
              {picked
                ? picked.setup.subtitle
                : "Connect it from where it runs today, or build one here."}
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

        {picked && <Form item={picked} onBusyChange={setBusy} />}
      </Box>
    </Box>
  );
}
