import PropTypes from "prop-types";
import { useRef, useState } from "react";
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
  useSetActorLimits,
  useUploadEnvironmentFile,
} from "src/api/simulate-environments/versions";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import SectionCard from "../../components/SectionCard";
import { refusalText } from "../evals/refusalText";
import ActorForm from "./ActorForm";
import ActorLimits from "./ActorLimits";
import ActorRelations from "./ActorRelations";
import ActorsSkeleton from "./ActorsSkeleton";
import { hidden, seenValue, speaks } from "./actorUtils";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
const CHANNEL_LABELS = ACTORS_COPY.callActors.channels;
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
export default function WorldActorsPanel({ envId, locked = false }) {
  const { data, isLoading, isError, refetch } = useActors(envId);
  const edit = useEditActor(envId);
  const limits = useSetActorLimits(envId);
  // A double click lands before the pending state disables the button: one request at a time, or the second is
  // refused as an edit made elsewhere.
  const sending = useRef(false);
  const once = (mutation, variables, options = {}) => {
    if (sending.current) return;
    sending.current = true;
    mutation.mutate(variables, {
      ...options,
      onSettled: () => {
        sending.current = false;
      },
    });
  };
  const [settings, setSettings] = useState(false);
  const upload = useUploadEnvironmentFile(envId);
  const [inUse, setInUse] = useState(null);
  // The record and the revision it was read at, taken together when the form opens: a refresh under an open form must
  // not move the revision, or a save would overwrite an edit it never saw.
  const [editing, setEditing] = useState(null);
  const openForm = (actor) => {
    // An error from a remove belongs to that remove, not to the form opening now.
    edit.reset();
    setInUse(null);
    setEditing({ actor, revision });
  };
  const cast = data?.actors || [];
  // Without a channel an actor cannot speak on the call, whatever side it is on: it acts in the world.
  // One list: the caller, people beside them and people and systems in the world are all actors.
  const revision = data?.revision ?? 0;
  // A refused save moves the form onto the record the refusal refreshed. Its key stays, so what was typed stays too.
  const conflicted = Boolean(editing) && edit.error?.error === "draft_changed";
  const fresh = conflicted
    ? cast.find((one) => one.id === editing.actor.id)
    : null;
  const gone = conflicted && Boolean(editing.actor.id) && !fresh;
  const form = conflicted
    ? { actor: fresh || editing.actor, revision }
    : editing;
  const context = {
    records: data?.record_schemas || {},
    actions: data?.actions || [],
    channels: data?.channels || {},
    loudness: data?.loudness || {},
    knowledgeSources: data?.knowledge_sources || { files: [], records: [] },
    others: cast,
    // Ids the environment keeps for itself (the agent, the caller): never an actor's.
    reservedIds: data?.reserved_ids || [],
    actorModels: data?.actor_models || [],
    // Uploads a document into the draft and answers with its path, so the form can pick it for this actor.
    upload: locked
      ? null
      : async (file) => {
          const before = new Set(data?.knowledge_sources?.files || []);
          // Sent at the open form's revision, so an edit made elsewhere since it opened is refused, not stepped over.
          const next = await upload.mutateAsync({
            file,
            kind: "knowledge",
            revision: editing?.revision ?? revision,
          });
          // The upload is a draft change of its own: the form saves on top of it.
          if (typeof next?.revision === "number") {
            setEditing((open) => open && { ...open, revision: next.revision });
          }
          return (
            (next?.knowledge_sources?.files || []).find(
              (one) => !before.has(one),
            ) || `knowledge/${file.name}`
          );
        },
  };
  const hasCaller = cast.some((one) => one.id === "caller");

  // Kept after the dialog closes, so its title doesn't blank while it fades out.
  const [removing, setRemoving] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const remove = (actor) => {
    if (actor.scenarios) {
      setInUse(C.inUse(actor.name || actor.id, actor.scenarios));
      return;
    }
    // Another actor answering instead of it, or brought in by it, would point at nobody.
    const naming = cast.filter(
      (one) =>
        one.id !== actor.id &&
        [one.activation?.fallback, one.parent, one.on_behalf_of].includes(
          actor.id,
        ),
    );
    if (naming.length) {
      setInUse(
        C.namedBy(
          actor.name || actor.id,
          naming.map((one) => one.name || one.id),
        ),
      );
      return;
    }
    setInUse(null);
    setRemoving(actor);
    setConfirmRemove(true);
  };
  const actionIds = context.actions.map((one) => one.action);
  // Another actor answering instead is named as people read it; anything else keeps its own label.
  const nameOf = (id) => cast.find((one) => one.id === id)?.name || id;
  // A new person on the caller's side starts on the first channel the rules list.
  const deletingId =
    edit.isPending && edit.variables?.op === "delete"
      ? edit.variables.actorId
      : null;
  const rows = (group) =>
    group.map((actor) => (
      <ActorRow
        key={actor.id}
        envId={envId}
        actor={actor}
        locked={locked}
        onEdit={() => openForm(actor)}
        onRemove={() => remove(actor)}
        actionIds={actionIds}
        loudChannels={context.loudness.channels || []}
        nameOf={nameOf}
        deleting={actor.id === deletingId}
        busy={edit.isPending}
      />
    ));

  // A cast already read stays on screen when a refresh fails; the revision check refuses an edit to a stale one.
  const failed = isError && !data;
  if (failed)
    return (
      <Box sx={{ p: 2 }}>
        <SectionCard title={C.title} subtitle={C.subtitle}>
          <ErrorState
            title={C.loadFailedTitle}
            detail={C.loadFailed}
            onRetry={() => refetch()}
          />
        </SectionCard>
      </Box>
    );
  if (isLoading) return <ActorsSkeleton locked={locked} />;

  return (
    <Box sx={{ p: 2 }}>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", maxWidth: 780, mb: 2 }}
      >
        {C.intro}
      </Typography>
      <Stack spacing={1} sx={{ mb: 2, "&:empty": { display: "none" } }}>
        {data?.source === "draft" && <Alert severity="info">{C.unsaved}</Alert>}
        {cast.length > 0 && !hasCaller && (
          <Alert severity="info">{ACTORS_COPY.noCaller}</Alert>
        )}
        {inUse && (
          <Alert severity="warning" onClose={() => setInUse(null)}>
            {inUse}
          </Alert>
        )}
        {edit.error && !editing && (
          <Alert severity="error" onClose={() => edit.reset()}>
            {refusalText(edit.error, C.saveFailed)}
          </Alert>
        )}
      </Stack>
      <Group
        title={C.title}
        about={C.everyoneAbout}
        empty={C.empty}
        locked={locked}
        busy={edit.isPending}
        addLabel={C.add}
        onAdd={() => openForm({})}
      >
        {rows(cast)}
      </Group>
      {cast.length > 1 && (
        <Box sx={{ mt: 2 }}>
          <ActorRelations cast={cast} />
        </Box>
      )}
      <Button
        size="small"
        sx={{ mt: 2 }}
        onClick={() => setSettings((open) => !open)}
      >
        {settings ? ACTORS_COPY.hideSettings : ACTORS_COPY.showSettings}
      </Button>
      <Collapse in={settings} unmountOnExit>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <ActorLimits
            key={revision}
            limits={data?.limits || {}}
            models={data?.actor_models || []}
            locked={locked}
            busy={limits.isPending}
            error={limits.error}
            onSave={(next, done) =>
              once(limits, { limits: next, revision }, { onSuccess: done })
            }
          />
        </Stack>
      </Collapse>
      <ActorForm
        key={form ? form.actor.id || "new" : "closed"}
        actor={form?.actor ?? null}
        context={context}
        busy={edit.isPending}
        error={
          editing && edit.error && !conflicted
            ? refusalText(edit.error, C.saveFailed)
            : null
        }
        conflict={gone ? "removed" : conflicted ? "changed" : null}
        onClose={() => {
          setEditing(null);
          edit.reset();
        }}
        onSave={(actor, shareActions) => {
          // A new save clears the refusal, so what it refreshed has to stay.
          setEditing(form);
          once(
            edit,
            {
              op: form.actor.id ? "replace" : "create",
              actor,
              actorId: form.actor.id,
              revision: form.revision,
              shareActions,
            },
            { onSuccess: () => setEditing(null) },
          );
        }}
      />
      <ConfirmDialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={removing ? C.removeTitle(removing.name || removing.id) : ""}
        content={C.removeBody}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              once(edit, { op: "delete", actorId: removing.id, revision });
              setConfirmRemove(false);
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
};

function Group({
  title,
  about,
  empty,
  locked,
  busy,
  addLabel,
  onAdd,
  children,
}) {
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
            disabled={busy}
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
          sx={{ px: 1.5, py: 1.5, typography: "s2", color: "text.secondary" }}
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
  busy: PropTypes.bool,
  addLabel: PropTypes.string,
  onAdd: PropTypes.func,
  children: PropTypes.node,
};

function summary(actor, actionIds, loudChannels, nameOf) {
  const permitted = actor.permitted_actions || [];
  const fallback = actor.activation?.fallback || "silent";
  // Every actor has both: how it speaks in the conversation (if it does) and what it may do in the world.
  return [
    speaks(actor) && (CHANNEL_LABELS[actor.channel] || actor.channel),
    speaks(actor) &&
      loudChannels.includes(actor.channel) &&
      (actor.loudness_ratio
        ? C.loudnessShare(actor.loudness_ratio)
        : C.loudnessAutomatic),
    `${C.mayDo}: ${
      permitted.length
        ? permitted
            .map((one) =>
              actionIds.includes(one) ? one : `${one} (${C.notInWorld})`,
            )
            .join(", ")
        : C.nothing
    }`,
    `${C.comesIn}: ${C.comesInLabel(actor.activation?.trigger)}`,
    `${C.ifNoAnswer}: ${
      permitted.includes(fallback)
        ? C.form.takesAction(fallback)
        : C.form.fallbackLabel(nameOf(fallback))
    }`,
    ACTORS_COPY.callActors.scenarios(actor.scenarios ?? 0),
  ]
    .filter(Boolean)
    .join(" · ");
}

function ActorRow({
  envId,
  actor,
  locked,
  onEdit,
  onRemove,
  actionIds,
  loudChannels,
  nameOf,
  deleting,
  busy,
}) {
  const [open, setOpen] = useState(false);
  // Off while it is being removed, so the removed actor's view is not read again.
  const {
    data: view,
    isLoading: viewLoading,
    isError: viewFailed,
    refetch: retryView,
  } = useActorView(envId, actor.id, {
    enabled: open && !deleting,
  });
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
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              wordBreak: "break-word",
            }}
          >
            {summary(actor, actionIds, loudChannels, nameOf)}
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
                {seenValue(one.input, one.value) || (
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
  nameOf: PropTypes.func.isRequired,
  deleting: PropTypes.bool,
  busy: PropTypes.bool,
};
