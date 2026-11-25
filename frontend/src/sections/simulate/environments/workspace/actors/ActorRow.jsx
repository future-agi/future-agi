import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { useActorView } from "src/api/simulate-environments/versions";
import ErrorState from "src/components/error-state";
import { ACTORS_COPY } from "./actors.constants";
import { TheirPart, WhatTheySee, WhatTheySeeSkeleton } from "./ActorSpecCards";

const C = ACTORS_COPY.worldActors;
// Only as wide as its text so the tooltip sits over the text.
const ONE_LINE = {
  display: "inline-block",
  maxWidth: "100%",
  verticalAlign: "top",
};

export default function ActorRow({
  envId,
  actor,
  callerLine = null,
  locked,
  onEdit,
  onRemove,
  loudChannels,
  deleting,
  busy,
  busyReason = null,
}) {
  const [open, setOpen] = useState(false);
  // Off while removing so the removed actor's view is not refetched.
  const {
    data: view,
    isLoading: viewLoading,
    isError: viewFailed,
    refetch: retryView,
  } = useActorView(envId, actor.id, {
    enabled: open && !deleting,
  });

  return (
    <Box>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        sx={{ px: 1.5, py: 1 }}
      >
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            flexShrink: 0,
            display: "grid",
            placeItems: "center",
            color: "text.tertiary",
            bgcolor: "background.neutral",
          }}
        >
          <Iconify icon="solar:users-group-two-rounded-linear" width={16} />
        </Box>
        <Box
          flex={1}
          minWidth={0}
          onClick={() => setOpen((o) => !o)}
          sx={{ cursor: "pointer" }}
        >
          <Box>
            <OverflowTooltip
              title={[actor.name || actor.id, actor.role]
                .filter(Boolean)
                .join(" · ")}
            >
              <Typography
                noWrap
                sx={{ typography: "s1", fontWeight: 600, ...ONE_LINE }}
              >
                {actor.name || actor.id}
                {actor.role ? (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 400 }}
                  >{` · ${actor.role}`}</Box>
                ) : null}
              </Typography>
            </OverflowTooltip>
          </Box>
          {callerLine || actor.goal ? (
            <Box>
              <OverflowTooltip title={callerLine || actor.goal}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    ...ONE_LINE,
                  }}
                >
                  {callerLine || actor.goal}
                </Typography>
              </OverflowTooltip>
            </Box>
          ) : null}
          {actor.arrival_overrides?.length ? (
            <CustomTooltip
              show
              size="small"
              arrow
              title={
                <Box component="span" sx={{ whiteSpace: "pre-line" }}>
                  {actor.arrival_overrides
                    .map((one) => `${one.scenario}: ${one.when_label}`)
                    .join("\n")}
                </Box>
              }
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  textDecoration: "underline dotted",
                  ...ONE_LINE,
                }}
              >
                {C.comesInDifferently(actor.arrival_overrides.length)}
              </Typography>
            </CustomTooltip>
          ) : null}
        </Box>
        <Stack
          direction="row"
          spacing={0.5}
          alignItems="center"
          sx={{ flexShrink: 0 }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              whiteSpace: "nowrap",
              mr: 0.5,
            }}
          >
            {callerLine
              ? actor.when_label
              : `${actor.when_label} · ${C.rowScenarios(actor.side, actor.scenarios)}`}
          </Typography>
          {!locked && (
            <>
              <CustomTooltip
                show
                size="small"
                arrow
                title={busyReason || C.edit}
              >
                <span>
                  <IconButton
                    size="small"
                    aria-label={C.editNamed(actor.name || actor.id)}
                    disabled={busy}
                    onClick={onEdit}
                  >
                    <Iconify
                      icon="solar:pen-new-square-linear"
                      width={16}
                      sx={{ color: busy ? "text.disabled" : "text.secondary" }}
                    />
                  </IconButton>
                </span>
              </CustomTooltip>
              <CustomTooltip
                show
                size="small"
                arrow
                title={busyReason || C.remove}
              >
                <span>
                  <IconButton
                    size="small"
                    aria-label={C.removeNamed(actor.name || actor.id)}
                    disabled={busy}
                    onClick={onRemove}
                  >
                    <Iconify
                      icon="solar:trash-bin-trash-linear"
                      width={16}
                      sx={{ color: busy ? "text.disabled" : "text.secondary" }}
                    />
                  </IconButton>
                </span>
              </CustomTooltip>
            </>
          )}
        </Stack>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box
          sx={{
            pl: 6,
            pr: 1.5,
            pb: 1.5,
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            gap: 1.5,
            alignItems: "start",
          }}
        >
          <TheirPart actor={actor} loudChannels={loudChannels} />
          {viewLoading && <WhatTheySeeSkeleton actor={actor} />}
          {viewFailed && !view && (
            <ErrorState
              title={C.viewFailedTitle}
              detail={C.loadFailed}
              onRetry={() => retryView()}
            />
          )}
          {view && <WhatTheySee actor={actor} view={view} />}
        </Box>
      </Collapse>
    </Box>
  );
}

ActorRow.propTypes = {
  callerLine: PropTypes.string,
  envId: PropTypes.string.isRequired,
  actor: PropTypes.object.isRequired,
  locked: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  loudChannels: PropTypes.arrayOf(PropTypes.string),
  deleting: PropTypes.bool,
  busy: PropTypes.bool,
  busyReason: PropTypes.string,
};
