import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, IconButton, Stack, Tab, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import SideDrawer from "../components/SideDrawer";
import TemplateBuildPanel from "./TemplateBuildPanel";
import WorldPreview, { SampleRecipe } from "./WorldPreview";
import { QuietTag } from "./LandingBits";
import { READINESS, adoptBlockOf, catalogueById } from "../_mock/catalogue";

/*
  A prebuilt world, opened from its card (catalogue.md): what it is first —
  the task package — then its sample agent, apart from the world, then Try
  it now or Use it with your agent. A task that can't be adopted (Coming
  soon, Withdrawn) says why and can't be started; a retired release can
  still be used, with a notice.
*/
export default function WorldDrawer({ world, onClose, onOpenWorld }) {
  const [tab, setTab] = useState("world");
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
                {entry && entry.release.status !== "ready" && (
                  <QuietTag>{READINESS[entry.release.status].label}</QuietTag>
                )}
              </Stack>
              <Typography
                noWrap
                sx={{ typography: "s2", color: "text.secondary" }}
              >
                {entry
                  ? `${entry.id} · ${entry.task} · release ${entry.release.version}`
                  : "Try it now with its sample agent, or use it with one of your agents."}
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
            </Stack>
          )}

          <CustomTabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{
              px: 2.5,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Tab value="world" label="World" />
            <Tab value="sample" label="Sample agent" />
            <Tab value="start" label="Try or use" />
          </CustomTabs>

          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
            {tab === "world" && <WorldPreview world={world} />}
            {tab === "sample" && <SampleRecipe world={world} />}
            {tab === "start" && (
              <TemplateBuildPanel
                key={world.id}
                template={world.template}
                entry={entry}
                blocked={blocked}
              />
            )}
          </Box>
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
