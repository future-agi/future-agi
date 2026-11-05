import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, IconButton, Stack, Tab, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import SideDrawer from "../components/SideDrawer";
import TemplateBuildPanel from "./TemplateBuildPanel";
import WorldPreview from "./WorldPreview";
import WorldRlAccess from "./WorldRlAccess";
import { QuietTag } from "./LandingBits";
import {
  HOST_SUPPORT,
  READINESS,
  adoptBlockOf,
  catalogueById,
} from "../_mock/catalogue";

/*
  A prebuilt world, opened from its card (catalogue.md): what it is first —
  the task package — then Use it with your agent. A task that can't be adopted (Coming
  soon, Withdrawn) says why and can't be started; a retired release can
  still be used, with a notice.
*/
export default function WorldDrawer({ world, onClose, onOpenWorld }) {
  const [tab, setTab] = useState("world");
  /* Access asked for, by world — the request goes to the owner named on it. */
  const [asked, setAsked] = useState({});
  useEffect(() => setTab("world"), [world?.id]);
  const entry = world?.entry || null;
  const blocked = adoptBlockOf(entry);
  const replacement = entry?.release?.replacement
    ? catalogueById(entry.release.replacement)
    : null;
  return (
    <SideDrawer open={!!world} onClose={onClose} width={640}>
      {world && (
        <Box
          sx={{
            height: "100%",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2.5,
              py: 1.75,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
                  {world.name}
                </Typography>
                <QuietTag>{world.typeLabel}</QuietTag>
                {entry?.host ? (
                  <QuietTag>{HOST_SUPPORT[entry.host.support]}</QuietTag>
                ) : (
                  entry &&
                  entry.release.status !== "ready" && (
                    <QuietTag>{READINESS[entry.release.status].label}</QuietTag>
                  )
                )}
              </Stack>
              <Typography
                noWrap
                sx={{ typography: "s2", color: "text.secondary" }}
              >
                {entry?.host
                  ? `${entry.host.name} · ${entry.task} · release ${entry.release.version}`
                  : entry
                    ? `${entry.id} · ${entry.task} · release ${entry.release.version}`
                    : "Use it with one of your agents."}
              </Typography>
            </Box>
            <IconButton
              size="small"
              onClick={onClose}
              aria-label="Close"
              sx={{ flexShrink: 0 }}
            >
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          </Stack>

          {/* Why it can't be started, or what a retired release means. */}
          {(blocked || entry?.release?.status === "retired") && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="flex-start"
              sx={{
                px: 2.5,
                py: 1.25,
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
                flexShrink: 0,
              }}
            >
              <Iconify
                icon="solar:info-circle-linear"
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
              />
              <Typography
                sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
              >
                {blocked || entry.release.note}
                {replacement && onOpenWorld && (
                  <>
                    {" "}
                    <Box
                      component="span"
                      role="button"
                      tabIndex={0}
                      onClick={() => onOpenWorld(replacement.id)}
                      onKeyDown={(e) =>
                        e.key === "Enter" && onOpenWorld(replacement.id)
                      }
                      sx={{
                        color: "text.primary",
                        textDecoration: "underline",
                        textUnderlineOffset: "3px",
                        cursor: "pointer",
                      }}
                    >
                      {`Use ${replacement.id} · ${replacement.task} instead`}
                    </Box>
                  </>
                )}
              </Typography>
              {entry?.release?.status === "access" && (
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!!asked[entry.id]}
                  onClick={() => setAsked((a) => ({ ...a, [entry.id]: true }))}
                  sx={{
                    flexShrink: 0,
                    color: "text.primary",
                    borderColor: "divider",
                  }}
                >
                  {asked[entry.id] ? "Requested" : "Request access"}
                </Button>
              )}
            </Stack>
          )}

          <CustomTabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{
              boxShadow: "none",
              px: 2.5,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Tab value="world" label="World" />
            <Tab value="start" label="Use with your agent" />
            {/* rl.md step 1: a trainer starts from the catalogue. */}
            {entry && <Tab value="rl" label="RL access" />}
          </CustomTabs>

          {/* Keyed by tab, so switching starts the new tab at its top. */}
          <Box key={tab} sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
            {tab === "world" && <WorldPreview world={world} />}
            {tab === "rl" && <WorldRlAccess world={world} />}
            {tab === "start" && (
              <TemplateBuildPanel
                key={world.id}
                template={world.template}
                entry={entry}
                blocked={blocked}
              />
            )}
          </Box>
          {/* Read the world, then the next step is always in reach. */}
          {tab === "world" && (
            <Stack
              direction="row"
              justifyContent="flex-end"
              sx={{
                pl: 2.5,
                /* Clear of the floating help button in the corner. */
                pr: 9,
                py: 1.5,
                borderTop: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={entry?.release?.status === "access"}
                onClick={() => setTab("start")}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
              >
                Use with your agent
              </Button>
            </Stack>
          )}
        </Box>
      )}
    </SideDrawer>
  );
}
WorldDrawer.propTypes = {
  world: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  /* Open another catalogue task (a withdrawn task's replacement). */
  onOpenWorld: PropTypes.func,
};
