import PropTypes from "prop-types";
import { useState } from "react";
import { Alert, Box, Button, Collapse, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useActors, useActorView, useEditActor } from "src/api/simulate-environments/versions";
import SideDrawer from "../../components/SideDrawer";
import SectionCard from "../../components/SectionCard";
import { CONTRACT_COPY } from "./contract.constants";

const C = CONTRACT_COPY.worldActors;
const lines = (value) => (value || "").split("\n").map((one) => one.trim()).filter(Boolean);
const list = (value) => (value || "").split(",").map((one) => one.trim()).filter(Boolean);

/**
 * The environment's actors, read from and edited in its draft (the cast lives in the
 * environment's `actors.json`). A saved version changes only when the draft is saved
 * from the Save bar. Each row opens what that actor sees and never sees.
 */
export default function WorldActorsPanel({ envId, locked = false }) {
  const { data } = useActors(envId);
  const edit = useEditActor(envId);
  const [editing, setEditing] = useState(null);
  const actors = data?.actors || [];
  const revision = data?.revision ?? 0;

  const remove = (actorId) => edit.mutate({ op: "delete", actorId, revision });

  return (
    <SectionCard title={C.title(actors.length)} subtitle={C.subtitle}>
      <Stack spacing={1.5} sx={{ px: 2.5, py: 1.5 }}>
        {data?.source === "draft" && <Alert severity="info">{C.unsaved}</Alert>}
        {edit.error && !editing && <Alert severity="error">{edit.error.message}</Alert>}
        {actors.length === 0 && <Typography sx={{ typography: "s2", color: "text.secondary" }}>{C.empty}</Typography>}
        {actors.map((actor) => (
          <ActorRow
            key={actor.id}
            envId={envId}
            actor={actor}
            locked={locked}
            onEdit={() => setEditing(actor)}
            onRemove={() => remove(actor.id)}
          />
        ))}
        {!locked && (
          <Box>
            <Button size="small" variant="outlined" onClick={() => setEditing({})}>
              {C.add}
            </Button>
          </Box>
        )}
      </Stack>
      <ActorForm
        actor={editing}
        busy={edit.isPending}
        error={editing ? edit.error?.message : null}
        onClose={() => {
          setEditing(null);
          edit.reset();
        }}
        onSave={(actor) =>
          edit.mutate(
            { op: editing?.id ? "replace" : "create", actor, actorId: editing?.id, revision },
            { onSuccess: () => setEditing(null) },
          )
        }
      />
    </SectionCard>
  );
}

WorldActorsPanel.propTypes = { envId: PropTypes.string.isRequired, locked: PropTypes.bool };

function ActorRow({ envId, actor, locked, onEdit, onRemove }) {
  const [open, setOpen] = useState(false);
  const { data: view } = useActorView(envId, actor.id, null, { enabled: open });
  const permitted = actor.permitted_actions || [];
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, p: 1.5 }}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box flex={1} minWidth={0} onClick={() => setOpen((o) => !o)} sx={{ cursor: "pointer" }}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {actor.name}
            {actor.role ? (
              <Box component="span" sx={{ color: "text.secondary", fontWeight: 400 }}>{` · ${actor.role}`}</Box>
            ) : null}
          </Typography>
          {actor.goal ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{`${C.wants}: ${actor.goal}`}</Typography>
          ) : null}
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${C.mayDo}: ${permitted.length ? permitted.join(", ") : C.nothing}`}
          </Typography>
        </Box>
        {!locked && (
          <>
            <Button size="small" onClick={onEdit}>{C.edit}</Button>
            <Button size="small" color="error" onClick={onRemove}>{C.remove}</Button>
          </>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit>
        {view && (
          <Stack spacing={0.75} sx={{ mt: 1.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{C.sees}</Typography>
            {view.sees.map((one) => (
              <Typography key={one.input} sx={{ typography: "s3", color: "text.secondary", wordBreak: "break-word" }}>
                {`${one.input}: ${typeof one.value === "string" ? one.value : JSON.stringify(one.value)}`}
              </Typography>
            ))}
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{C.neverSees}</Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>{view.never_sees.join(", ")}</Typography>
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
};

function ActorForm({ actor, busy, error, onClose, onSave }) {
  const existing = Boolean(actor?.id);
  const [fields, setFields] = useState({});
  const value = (name, fallback = "") => (name in fields ? fields[name] : fallback);
  const set = (name) => (event) => setFields((current) => ({ ...current, [name]: event.target.value }));
  const close = () => {
    setFields({});
    onClose();
  };
  const save = () => {
    const constraints = actor?.constraints || {};
    const activation = actor?.activation || {};
    const tool = value("tool", activation.trigger?.tool || "");
    // Only an edit to the tool field changes the trigger; any other kind (delay, contact, chance) is kept as it was.
    const trigger = "tool" in fields || !activation.trigger
      ? tool ? { kind: "on_tool", tool } : { kind: "always" }
      : activation.trigger;
    onSave({
      ...actor,
      id: value("id", actor?.id || ""),
      name: value("name", actor?.name || ""),
      role: value("role", actor?.role || ""),
      goal: value("goal", actor?.goal || ""),
      constraints: {
        ...constraints,
        may_agree: lines(value("may_agree", (constraints.may_agree || []).join("\n"))),
        private: lines(value("private", (constraints.private || []).join("\n"))),
      },
      permitted_actions: list(value("permitted", (actor?.permitted_actions || []).join(", "))),
      activation: {
        ...activation,
        trigger,
        fallback: value("fallback", activation.fallback || "silent"),
      },
    });
  };
  const F = C.form;
  return (
    <SideDrawer open={Boolean(actor)} onClose={close} width={{ xs: "100%", sm: 480 }}>
      {actor && (
        <Stack spacing={2} sx={{ p: 2.5 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>
            {existing ? F.editTitle(actor.name) : F.createTitle}
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField size="small" label={F.id} helperText={F.idHelp} disabled={existing} value={value("id", actor.id || "")} onChange={set("id")} />
          <TextField size="small" label={F.name} value={value("name", actor.name || "")} onChange={set("name")} />
          <TextField size="small" label={F.role} value={value("role", actor.role || "")} onChange={set("role")} />
          <TextField size="small" label={F.goal} multiline minRows={2} value={value("goal", actor.goal || "")} onChange={set("goal")} />
          <TextField
            size="small" label={F.mayAgree} multiline minRows={2}
            value={value("may_agree", (actor.constraints?.may_agree || []).join("\n"))} onChange={set("may_agree")}
          />
          <TextField
            size="small" label={F.private} multiline minRows={2}
            value={value("private", (actor.constraints?.private || []).join("\n"))} onChange={set("private")}
          />
          <TextField
            size="small" label={F.permitted}
            value={value("permitted", (actor.permitted_actions || []).join(", "))} onChange={set("permitted")}
          />
          <TextField
            size="small" label={F.trigger}
            value={value("tool", actor.activation?.trigger?.tool || "")} onChange={set("tool")}
          />
          <TextField
            select size="small" label={F.fallback}
            value={value("fallback", actor.activation?.fallback || "silent")} onChange={set("fallback")}
          >
            {Object.entries(F.fallbacks).map(([id, label]) => (
              <MenuItem key={id} value={id}>{label}</MenuItem>
            ))}
          </TextField>
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button onClick={close}>{F.cancel}</Button>
            <Button variant="contained" disabled={busy} onClick={save}>{F.save}</Button>
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}

ActorForm.propTypes = {
  actor: PropTypes.object,
  busy: PropTypes.bool,
  error: PropTypes.string,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
