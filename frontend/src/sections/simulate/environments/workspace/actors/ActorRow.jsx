import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  Collapse,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { useActorView } from "src/api/simulate-environments/versions";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { ACTORS_COPY } from "./actors.constants";
import { actorTitle } from "./actorUtils";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.callActors.channels;
// Only as wide as its text so the tooltip sits over the text.
const ONE_LINE = {
  display: "inline-block",
  maxWidth: "100%",
  verticalAlign: "top",
};

// How they take part, how loud, when they come in and how many scenarios, one short tag each.
function tagsOf(actor, loudChannels) {
  return [
    actor.channel
      ? CHANNEL_LABELS[actor.channel] || actor.channel
      : actor.side !== "caller" && C.actsInWorld,
    actor.channel &&
      loudChannels.includes(actor.channel) &&
      (actor.loudness_ratio
        ? C.loudnessShare(actor.loudness_ratio)
        : C.loudnessAutomatic),
    actor.when_label,
    ACTORS_COPY.callActors.scenarios(actor.scenarios),
  ].filter(Boolean);
}

// What the open row says about their part before what they see.
function partOf(actor) {
  const mayDo = actor.permissions.length
    ? actor.permissions
        .map((one) =>
          one.in_world ? one.action : `${one.action} (${C.notInWorld})`,
        )
        .join(", ")
    : C.nothing;
  return [`${C.mayDo}: ${mayDo}`, `${C.ifNoAnswer}: ${actor.fallback.label}`];
}

export default function ActorRow({
  envId,
  actor,
  locked,
  onEdit,
  onRemove,
  loudChannels,
  deleting,
  busy,
  byName = false,
}) {
  const [open, setOpen] = useState(false);
  // Under a role heading the role is already said, so the row goes by the person's name.
  const title = byName
    ? { main: actor.name || actor.id, speaksAs: "" }
    : actorTitle(actor);
  // Off while removing so the removed actor's view is not refetched.
  const {
    data: view,
    isLoading: viewLoading,
    isError: viewFailed,
    refetch: retryView,
  } = useActorView(envId, actor.id, {
    enabled: open && !deleting,
  });

  const quiet = {
    typography: "s3",
    color: "text.secondary",
    wordBreak: "break-word",
  };
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
              title={
                title.speaksAs
                  ? `${title.main} · ${C.speaksAs(title.speaksAs)}`
                  : title.main
              }
            >
              <Typography
                noWrap
                sx={{ typography: "s1", fontWeight: 600, ...ONE_LINE }}
              >
                {title.main}
                {title.speaksAs ? (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 400 }}
                  >{` · ${C.speaksAs(title.speaksAs)}`}</Box>
                ) : null}
              </Typography>
            </OverflowTooltip>
          </Box>
          {actor.goal ? (
            <Box>
              <OverflowTooltip title={`${C.wants}: ${actor.goal}`}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    ...ONE_LINE,
                  }}
                >
                  {`${C.wants}: ${actor.goal}`}
                </Typography>
              </OverflowTooltip>
            </Box>
          ) : null}
          <Stack
            direction="row"
            flexWrap="wrap"
            useFlexGap
            spacing={0.75}
            sx={{ mt: 0.5 }}
          >
            {tagsOf(actor, loudChannels).map((tag) => (
              <Chip
                key={tag}
                // A long comes-in text is cut off with an ellipsis and shows in full on hover.
                label={
                  <OverflowTooltip title={tag}>
                    <Box
                      component="span"
                      sx={{
                        display: "block",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {tag}
                    </Box>
                  </OverflowTooltip>
                }
                size="small"
                sx={{
                  bgcolor: "background.neutral",
                  color: "text.secondary",
                  maxWidth: "100%",
                }}
              />
            ))}
          </Stack>
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
          <Button size="small" onClick={() => setOpen((o) => !o)}>
            {open ? C.hideView : C.showView}
          </Button>
          {!locked && (
            <>
              <CustomTooltip show size="small" arrow title={C.edit}>
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
              <CustomTooltip show size="small" arrow title={C.remove}>
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
        <Stack spacing={0.75} sx={{ pl: 6.5, pr: 1.5, pb: 1 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {C.theirPart}
          </Typography>
          {partOf(actor).map((line) => (
            <Typography key={line} sx={quiet}>
              {line}
            </Typography>
          ))}
        </Stack>
        {viewLoading && <ContentSkeleton variant="panel" sx={{ pl: 5 }} />}
        {viewFailed && !view && (
          <ErrorState
            title={C.viewFailedTitle}
            detail={C.loadFailed}
            onRetry={() => retryView()}
          />
        )}
        {view && (
          <Stack spacing={0.75} sx={{ pl: 6.5, pr: 1.5, pb: 1.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.sees}
            </Typography>
            {view.unused_reason && (
              <Typography sx={quiet}>{view.unused_reason}</Typography>
            )}
            {view.sees.map((one) => (
              <Typography key={one.input} sx={quiet}>
                {`${C.inputs[one.input] || one.input}: `}
                {one.text || (
                  <Box component="span" sx={{ color: "text.disabled" }}>
                    {C.notSet}
                  </Box>
                )}
              </Typography>
            ))}
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.neverSees}
            </Typography>
            {view.never_sees.grader.length > 0 && (
              <Typography
                sx={quiet}
              >{`${C.graderMaterial}: ${view.never_sees.grader.join(", ")}`}</Typography>
            )}
            {view.never_sees.others.map((one) => (
              <Typography key={one.fields.join(", ")} sx={quiet}>
                {C.othersPrivate(one.fields.join(", "), one.count)}
              </Typography>
            ))}
          </Stack>
        )}
      </Collapse>
    </Box>
  );
}

ActorRow.propTypes = {
  envId: PropTypes.string.isRequired,
  actor: PropTypes.object.isRequired,
  locked: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  loudChannels: PropTypes.arrayOf(PropTypes.string),
  deleting: PropTypes.bool,
  busy: PropTypes.bool,
  // Shown under its role's heading, titled by name.
  byName: PropTypes.bool,
};
