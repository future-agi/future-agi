import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Alert, Box, Button, Collapse, Stack, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useActors,
  useEditActor,
  useSetActorLimits,
  useAcceptUnresolved,
  useUploadEnvironmentFile,
} from "src/api/simulate-environments/versions";
import ErrorState from "src/components/error-state";
import SectionCard from "../../components/SectionCard";
import { refusalText } from "../evals/refusalText";
import ActorForm from "./ActorForm";
import ActorLimits from "./ActorLimits";
import ActorRelations from "./ActorRelations";
import ActorRow from "./ActorRow";
import ActorSideGroups from "./ActorSideGroups";
import ActorsSkeleton from "./ActorsSkeleton";
import AddActorMenu from "./AddActorMenu";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
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
  const accept = useAcceptUnresolved(envId);
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
  // A new actor starts on its side's default way of joining, as the cast gives it.
  const addOn = (side) =>
    openForm({ side, channel: data.side_defaults[side].channel });
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
  // Read only once the cast has loaded: everything an actor can be given comes with it.
  const context = data && {
    actions: data.actions,
    channelOptions: data.channel_options,
    loudnessRange: data.loudness.automatic,
    idHint: data.id_hint,
    knowledgeSources: data.knowledge_sources,
    others: cast,
    actorModels: data.actor_models,
    // Uploads a document into the draft and answers with where it was stored, so the form can pick it for this actor.
    upload: locked
      ? null
      : async (file) => {
          // Sent at the open form's revision, so an edit made elsewhere since it opened is refused, not stepped over.
          const next = await upload.mutateAsync({
            file,
            kind: "knowledge",
            revision: editing ? editing.revision : revision,
          });
          // The upload is a draft change of its own: the form saves on top of it.
          setEditing((open) => open && { ...open, revision: next.revision });
          return next.uploaded_path;
        },
  };

  // Kept after the dialog closes, so its title doesn't blank while it fades out.
  const [removing, setRemoving] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // The cast says whether an actor can go and, when not, why: a scenario includes it, or another actor names it.
  const remove = (actor) => {
    if (!actor.removable) {
      setInUse(actor.removal_blocked_by.join(" "));
      return;
    }
    setInUse(null);
    setRemoving(actor);
    setConfirmRemove(true);
  };
  // The ways of joining that have a loudness.
  const loudChannels = (data ? data.channel_options : [])
    .filter((one) => one.loudness)
    .map((one) => one.channel);
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
        loudChannels={loudChannels}
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
        {(edit.isPaused || limits.isPaused) && (
          <Alert severity="info">{C.offline}</Alert>
        )}
        {data?.source === "draft" && <Alert severity="info">{C.unsaved}</Alert>}
        {cast.length > 0 && !data.has_caller && (
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
      {(data?.unresolved || []).length > 0 && (
        <Box sx={{ mb: 2 }} data-testid="needs-decision">
          <SectionCard title={C.unresolved.title} subtitle={C.unresolved.about}>
            <Stack spacing={1.5}>
              {data.unresolved.map((point) => {
                const about = cast.find((one) => one.id === point.actor);
                return (
                  <Box key={point.policy}>
                    <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                      {point.policy}
                    </Typography>
                    <Typography
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      {point.why}
                      {about
                        ? ` · ${C.unresolved.about_actor(about.name || about.id)}`
                        : ""}
                    </Typography>
                    {!locked && (
                      <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
                        <Button
                          size="small"
                          variant="outlined"
                          sx={OUTLINED}
                          disabled={accept.isPending}
                          onClick={() =>
                            once(accept, { policy: point.policy, revision })
                          }
                        >
                          {C.unresolved.accept}
                        </Button>
                        {/* A point about no actor in the cast adds one, its side picked first. */}
                        {about ? (
                          <Button
                            size="small"
                            variant="outlined"
                            sx={OUTLINED}
                            onClick={() => openForm(about)}
                          >
                            {C.unresolved.edit}
                          </Button>
                        ) : (
                          <AddActorMenu label={C.add} onPick={addOn} />
                        )}
                      </Stack>
                    )}
                  </Box>
                );
              })}
              {accept.error && (
                <Alert severity="error" onClose={() => accept.reset()}>
                  {refusalText(accept.error, C.saveFailed)}
                </Alert>
              )}
            </Stack>
          </SectionCard>
        </Box>
      )}
      <SectionCard
        title={C.groupTitle(C.title, cast.length)}
        subtitle={C.everyoneAbout}
        sx={{ mb: 2 }}
        action={
          !locked && (
            <AddActorMenu
              label={C.add}
              disabled={edit.isPending}
              onPick={addOn}
            />
          )
        }
      >
        {cast.length === 0 ? (
          <Typography
            sx={{ px: 1.5, py: 1.5, typography: "s2", color: "text.secondary" }}
          >
            {C.empty}
          </Typography>
        ) : (
          <ActorSideGroups cast={cast} renderRows={rows} />
        )}
      </SectionCard>
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
            limits={data.limits}
            fields={data.limit_fields}
            models={data.actor_models}
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
        envId={envId}
        actor={form ? form.actor : null}
        context={context}
        busy={edit.isPending}
        offline={edit.isPaused}
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
        onSave={(request, shareActions) => {
          // A new save clears the refusal, so what it refreshed has to stay.
          setEditing(form);
          once(
            edit,
            { ...request, revision: form.revision, shareActions },
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
