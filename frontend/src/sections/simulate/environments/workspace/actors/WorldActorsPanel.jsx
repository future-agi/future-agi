import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import {
  useActors,
  useActorView,
  useEditActor,
} from "src/api/simulate-environments/versions";
import SectionCard from "../../components/SectionCard";
import { genderLabel } from "../scenarios/personaLabels";
import ActorForm from "./ActorForm";
import { hidden, readable, speaks } from "./actorUtils";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.subActors.channels;
// Only as wide as its text, so the tooltip sits over the text, not the middle of the row.
const ONE_LINE = {
  display: "inline-block",
  maxWidth: "100%",
  verticalAlign: "top",
};
const OUTLINED = {
  color: "text.primary",
  borderColor: "divider",
  flexShrink: 0,
};

/**
 * The environment's cast, read from and edited in its draft: people on the caller's side who
 * speak on the call, and people and systems in the world who act through its actions. A saved
 * version changes only when the draft is saved from the Save bar. Each row opens what that actor
 * sees and never sees.
 */
export default function WorldActorsPanel({
  envId,
  locked = false,
  callerSide = false,
}) {
  const { data, isLoading, isError } = useActors(envId);
  const edit = useEditActor(envId);
  const [editing, setEditing] = useState(null);
  const cast = data?.actors || [];
  const caller = cast.filter(speaks);
  // Without a channel an actor cannot speak on the call, whatever side it is on: it acts in the world.
  const world = cast.filter((one) => !speaks(one));
  const revision = data?.revision ?? 0;
  const context = {
    records: data?.record_schemas || {},
    actions: data?.actions || [],
    channels: data?.channels || {},
    loudness: data?.loudness || {},
    others: cast,
  };

  const [inUse, setInUse] = useState(null);
  const [removing, setRemoving] = useState(null);
  const remove = (actor) => {
    if (actor.scenarios) {
      setInUse(C.inUse(actor.name || actor.id, actor.scenarios));
      return;
    }
    setInUse(null);
    setRemoving(actor);
  };
  const actionIds = context.actions.map((one) => one.action);
  // A new person on the caller's side starts on the first channel the rules list.
  const firstChannel = Object.keys(context.channels)[0];
  const rows = (group) =>
    group.map((actor) => (
      <ActorRow
        key={actor.id}
        envId={envId}
        actor={actor}
        locked={locked}
        onEdit={() => setEditing(actor)}
        onRemove={() => remove(actor)}
        actionIds={actionIds}
        loudChannels={context.loudness.channels || []}
      />
    ));

  // A cast already read stays on screen when a refresh fails; the revision check refuses an edit to a stale one.
  const failed = isError && !data;
  if (isLoading || failed)
    return (
      <Box sx={{ p: 2 }}>
        <SectionCard title={C.title} subtitle={C.subtitle}>
          <Box sx={{ px: 1.5, py: 1.5 }}>
            {failed ? (
              <Alert severity="error">{C.loadFailed}</Alert>
            ) : (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {C.loading}
              </Typography>
            )}
          </Box>
        </SectionCard>
      </Box>
    );

  return (
    <Box sx={{ p: 2 }}>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", maxWidth: 780, mb: 2 }}
      >
        {C.intro}
      </Typography>
      <Stack spacing={1} sx={{ mb: 2, "&:empty": { display: "none" } }}>
        {data?.source === "draft" && <Alert severity="info">{C.unsaved}</Alert>}
        {inUse && (
          <Alert severity="warning" onClose={() => setInUse(null)}>
            {inUse}
          </Alert>
        )}
        {edit.error && !editing && (
          <Alert severity="error">{edit.error.message}</Alert>
        )}
      </Stack>
      {(callerSide || caller.length > 0) && (
        <Group
          title={C.callerSide}
          about={C.callerSideAbout}
          empty={C.callerSideEmpty}
          locked={locked || !firstChannel}
          addLabel={C.addCallerSide}
          onAdd={() => setEditing({ channel: firstChannel })}
        >
          {rows(caller)}
        </Group>
      )}
      <Group
        title={C.worldSide}
        about={C.worldSideAbout}
        empty={C.empty}
        locked={locked}
        addLabel={C.add}
        onAdd={() => setEditing({})}
      >
        {rows(world)}
      </Group>
      <ActorForm
        key={
          editing
            ? editing.id || `new-${editing.channel ? "caller" : "world"}`
            : "closed"
        }
        actor={editing}
        context={context}
        busy={edit.isPending}
        error={editing ? edit.error?.message : null}
        onClose={() => {
          setEditing(null);
          edit.reset();
        }}
        onSave={(actor) =>
          edit.mutate(
            {
              op: editing?.id ? "replace" : "create",
              actor,
              actorId: editing?.id,
              revision,
            },
            { onSuccess: () => setEditing(null) },
          )
        }
      />
      <ConfirmDialog
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        title={removing ? C.removeTitle(removing.name || removing.id) : ""}
        content={C.removeBody}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              edit.mutate({ op: "delete", actorId: removing.id, revision });
              setRemoving(null);
            }}
          >
            {C.remove}
          </Button>
        }
      />
    </Box>
  );
}

WorldActorsPanel.propTypes = {
  envId: PropTypes.string.isRequired,
  locked: PropTypes.bool,
  // The environment can have people on the caller's side (a voice call).
  callerSide: PropTypes.bool,
};

function Group({ title, about, empty, locked, addLabel, onAdd, children }) {
  const count = Array.isArray(children) ? children.length : 0;
  return (
    <SectionCard
      title={C.groupTitle(title, count)}
      subtitle={about}
      sx={{ mb: 2 }}
      action={
        !locked && (
          <Button
            size="small"
            variant="outlined"
            onClick={onAdd}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={OUTLINED}
          >
            {addLabel}
          </Button>
        )
      }
    >
      {count === 0 ? (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", px: 1.5, py: 1.5 }}
        >
          {empty}
        </Typography>
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {children}
        </Stack>
      )}
    </SectionCard>
  );
}

Group.propTypes = {
  title: PropTypes.string,
  about: PropTypes.string,
  empty: PropTypes.string,
  locked: PropTypes.bool,
  addLabel: PropTypes.string,
  onAdd: PropTypes.func,
  children: PropTypes.node,
};

function summary(actor, actionIds, loudChannels) {
  if (speaks(actor))
    return [
      CHANNEL_LABELS[actor.channel] || actor.channel,
      loudChannels.includes(actor.channel) &&
        (actor.loudness_ratio
          ? C.loudnessShare(actor.loudness_ratio)
          : C.loudnessAutomatic),
      genderLabel(actor.persona?.gender),
      actor.persona?.age_group,
      actor.persona?.accent,
      ACTORS_COPY.subActors.scenarios(actor.scenarios ?? 0),
    ]
      .filter(Boolean)
      .join(" · ");
  const permitted = actor.permitted_actions || [];
  const tool = actor.activation?.trigger?.tool;
  const fallback = actor.activation?.fallback || "silent";
  return [
    `${C.mayDo}: ${
      permitted.length
        ? permitted
            .map((one) =>
              actionIds.includes(one) ? one : `${one} (${C.notInWorld})`,
            )
            .join(", ")
        : C.nothing
    }`,
    `${C.comesIn}: ${tool ? C.whenCalled(tool) : C.atStart}`,
    `${C.ifNoAnswer}: ${C.form.fallbackLabel(fallback)}`,
  ].join(" · ");
}

function ActorRow({
  envId,
  actor,
  locked,
  onEdit,
  onRemove,
  actionIds,
  loudChannels,
}) {
  const [open, setOpen] = useState(false);
  const { data: view } = useActorView(envId, actor.id, { enabled: open });
  const shown = hidden(view?.never_sees);
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
              title={[actor.name, actor.role].filter(Boolean).join(" · ")}
            >
              <Typography
                noWrap
                sx={{ typography: "s1", fontWeight: 600, ...ONE_LINE }}
              >
                {actor.name}
                {actor.role ? (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 400 }}
                  >{` · ${actor.role}`}</Box>
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
                    typography: "s1",
                    color: "text.secondary",
                    ...ONE_LINE,
                  }}
                >
                  {`${C.wants}: ${actor.goal}`}
                </Typography>
              </OverflowTooltip>
            </Box>
          ) : null}
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              wordBreak: "break-word",
            }}
          >
            {summary(actor, actionIds, loudChannels)}
          </Typography>
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
                <IconButton size="small" aria-label={C.edit} onClick={onEdit}>
                  <Iconify
                    icon="solar:pen-new-square-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              </CustomTooltip>
              <CustomTooltip show size="small" arrow title={C.remove}>
                <IconButton
                  size="small"
                  aria-label={C.remove}
                  onClick={() => {
                    // Closed first, so the removed actor's view is not read again.
                    setOpen(false);
                    onRemove();
                  }}
                >
                  <Iconify
                    icon="solar:trash-bin-trash-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              </CustomTooltip>
            </>
          )}
        </Stack>
      </Stack>
      <Collapse in={open} unmountOnExit>
        {view && (
          <Stack spacing={0.75} sx={{ pl: 6.5, pr: 1.5, pb: 1.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.sees}
            </Typography>
            {view.sees.map((one) => (
              <Typography key={one.input} sx={quiet}>
                {`${C.inputs[one.input] || one.input}: `}
                {readable(one.value) || (
                  <Box component="span" sx={{ color: "text.disabled" }}>
                    {C.notSet}
                  </Box>
                )}
              </Typography>
            ))}
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {C.neverSees}
            </Typography>
            {shown.grader.length > 0 && (
              <Typography
                sx={quiet}
              >{`${C.graderMaterial}: ${shown.grader.join(", ")}`}</Typography>
            )}
            {shown.others.map(([fields, count]) => (
              <Typography key={fields} sx={quiet}>
                {C.othersPrivate(fields, count)}
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
  actionIds: PropTypes.arrayOf(PropTypes.string),
  loudChannels: PropTypes.arrayOf(PropTypes.string),
};
