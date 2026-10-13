import { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

const empty = () => ({ name: "", description: "", instructions: "" });
const SCHEMA = "omega-workspace-skills/v1";
const validSummary = (skill) =>
  skill &&
  typeof skill.id === "string" &&
  /^skill_[a-f0-9-]{36}$/u.test(skill.id) &&
  Number.isSafeInteger(skill.revision) &&
  skill.revision > 0 &&
  typeof skill.name === "string" &&
  skill.name.trim() &&
  typeof skill.description === "string" &&
  skill.description.trim() &&
  typeof skill.digest === "string" &&
  /^sha256:[a-f0-9]{64}$/u.test(skill.digest) &&
  ["active", "archived"].includes(skill.status);
const validDetail = (result) =>
  result?.available === true &&
  result.schemaVersion === SCHEMA &&
  validSummary(result.skill) &&
  !inputError(result.skill);
const reason = (result) =>
  typeof result?.reason === "string"
    ? result.reason
    : "The workspace skill library is unavailable.";
function inputError(draft) {
  for (const [key, max] of [
    ["name", 200],
    ["description", 2000],
    ["instructions", 16000],
  ]) {
    if (typeof draft[key] !== "string" || !draft[key].trim())
      return "Name, description and instructions are required.";
    if (new TextEncoder().encode(draft[key]).length > max)
      return `${key} exceeds ${max.toLocaleString()} UTF-8 bytes.`;
  }
  return "";
}

export default function SkillLibraryPanel({ client }) {
  const [state, setState] = useState({
    loading: true,
    result: null,
    error: "",
  });
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const activeClient = useRef(client);
  activeClient.current = client;
  const load = useCallback(async () => {
    const request = ++generation.current;
    setState({ loading: true, result: null, error: "" });
    try {
      const result = await client.listWorkspaceSkills();
      if (activeClient.current !== client || generation.current !== request)
        return;
      const valid =
        result?.available === true &&
        result.schemaVersion === SCHEMA &&
        Array.isArray(result.skills) &&
        result.skills.length <= 200 &&
        result.skills.every(validSummary) &&
        new Set(result.skills.map((skill) => skill.id)).size ===
          result.skills.length &&
        typeof result.canManage === "boolean";
      setState({
        loading: false,
        result: valid ? result : null,
        error: valid ? "" : reason(result),
      });
    } catch {
      if (activeClient.current === client && generation.current === request)
        setState({
          loading: false,
          result: null,
          error: "Couldn't read workspace skills. Try again.",
        });
    }
  }, [client]);
  useEffect(() => {
    setDraft(null);
    setBusy(false);
    setNotice("");
    void load();
    return () => {
      generation.current += 1;
    };
  }, [load]);
  const canManage = state.result?.canManage === true;
  const beginCreate = () => {
    setNotice("");
    setDraft({
      ...empty(),
      mode: "create",
      key:
        globalThis.crypto?.randomUUID?.() ||
        `skill-${Date.now()}-${Math.random()}`,
      client,
    });
  };
  const inspect = async (skill) => {
    setBusy(true);
    setNotice("");
    const request = ++generation.current;
    try {
      const result = await client.getWorkspaceSkill(skill.id);
      if (activeClient.current !== client || generation.current !== request)
        return;
      const entry = result?.skill;
      if (
        !validDetail(result) ||
        entry?.id !== skill.id ||
        typeof result.canManage !== "boolean"
      ) {
        setNotice(reason(result));
        return;
      }
      setDraft({
        ...entry,
        mode: "inspect",
        client,
        canManage: result.canManage === true,
      });
    } catch {
      if (activeClient.current === client)
        setNotice("Couldn't read this skill. Select it again.");
    } finally {
      if (activeClient.current === client && generation.current === request)
        setBusy(false);
    }
  };
  const save = async () => {
    if (
      !draft ||
      busy ||
      !canManage ||
      draft.client !== client ||
      inputError(draft)
    )
      return;
    const captured = draft;
    setBusy(true);
    setNotice("");
    const input = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      instructions: draft.instructions.trim(),
    };
    try {
      const result = draft.pendingReadback
        ? {
            available: true,
            schemaVersion: SCHEMA,
            skill: draft.pendingReadback,
          }
        : draft.mode === "create"
          ? await client.createWorkspaceSkill(input, draft.key)
          : await client.updateWorkspaceSkill(draft.id, {
              ...input,
              expectedRevision: draft.revision,
            });
      if (activeClient.current !== client) return;
      if (!result?.available) {
        setNotice(reason(result));
        return;
      }
      if (
        !validDetail(result) ||
        result.skill.status !== "active" ||
        (draft.mode !== "create" &&
          (result.skill.id !== draft.id ||
            result.skill.revision !== draft.revision + 1)) ||
        Object.entries(input).some(
          ([key, value]) => result.skill[key] !== value,
        )
      ) {
        setNotice(
          "The save response could not be verified. Read the current library before changing this skill again.",
        );
        return;
      }
      setDraft((current) =>
        current === captured
          ? { ...current, pendingReadback: result.skill }
          : current,
      );
      const readback = await client.getWorkspaceSkill(result.skill.id);
      if (activeClient.current !== client) return;
      if (
        !validDetail(readback) ||
        readback.skill.id !== result.skill.id ||
        readback.skill.revision !== result.skill.revision ||
        readback.skill.digest !== result.skill.digest ||
        Object.entries(input).some(
          ([key, value]) => readback.skill[key] !== value,
        )
      ) {
        setNotice(
          "A save response was received, but the stored revision could not be verified. Verify it again or close this draft and read the library.",
        );
        return;
      }
      setDraft(null);
      await load();
      if (activeClient.current === client)
        setNotice(
          captured.mode === "create"
            ? "Workspace skill created. Select its revision on an agent to use it."
            : "New skill revision saved. Existing agent selections stay pinned to their earlier revisions.",
        );
    } catch {
      if (activeClient.current === client)
        setNotice(
          "Couldn't save this skill. Your draft is kept here; retry safely.",
        );
    } finally {
      if (activeClient.current === client) setBusy(false);
    }
  };
  const archive = async () => {
    if (
      !draft ||
      !canManage ||
      draft.client !== client ||
      draft.status !== "active" ||
      busy
    )
      return;
    if (
      typeof globalThis.confirm === "function" &&
      !globalThis.confirm(
        "Archive this skill? New execution using any of its revisions will be refused until agents remove it or select another skill.",
      )
    )
      return;
    setBusy(true);
    setNotice("");
    try {
      const result = await client.archiveWorkspaceSkill(draft.id, {
        expectedRevision: draft.revision,
      });
      if (activeClient.current !== client) return;
      if (!result?.available) {
        setNotice(reason(result));
        return;
      }
      if (
        !validDetail(result) ||
        result.skill.id !== draft.id ||
        result.skill.revision !== draft.revision ||
        result.skill.status !== "archived"
      ) {
        setNotice(
          "The archive response could not be verified. Read the skill status before trying again.",
        );
        return;
      }
      const readback = await client.getWorkspaceSkill(draft.id);
      if (activeClient.current !== client) return;
      if (
        !validDetail(readback) ||
        readback.skill.id !== draft.id ||
        readback.skill.status !== "archived" ||
        readback.skill.revision !== draft.revision
      ) {
        setNotice(
          "The archived status could not be verified. Read the library before retrying.",
        );
        return;
      }
      setDraft(null);
      await load();
      if (activeClient.current === client)
        setNotice(
          "Skill archived. Historical revisions remain readable; new execution cannot use them.",
        );
    } catch {
      if (activeClient.current === client)
        setNotice(
          "Couldn't archive this skill. Read its status before retrying.",
        );
    } finally {
      if (activeClient.current === client) setBusy(false);
    }
  };
  const editable =
    draft &&
    canManage &&
    draft.client === client &&
    (draft.mode === "create" || (draft.canManage && draft.status === "active"));
  return (
    <Stack spacing={2} aria-label="Workspace skill library">
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1}
        justifyContent="space-between"
      >
        <Box>
          <Typography variant="h6">Workspace skills</Typography>
          <Typography variant="body2" color="text.secondary">
            Reusable instructions visible to members of this workspace. Skills
            do not grant tools, credentials or additional permissions.
          </Typography>
        </Box>
        <Button
          onClick={beginCreate}
          disabled={!canManage || busy || Boolean(draft)}
        >
          Create skill
        </Button>
      </Stack>
      {state.loading ? <Typography>Reading skills…</Typography> : null}
      {state.error ? (
        <Alert
          severity="info"
          action={<Button onClick={() => void load()}>Retry</Button>}
        >
          {state.error}
        </Alert>
      ) : null}
      {notice ? <Alert severity="info">{notice}</Alert> : null}
      {state.result?.skills.length === 0 ? (
        <Typography color="text.secondary">No workspace skills yet.</Typography>
      ) : null}
      {state.result?.skills.map((skill) => (
        <Card variant="outlined" key={skill.id}>
          <CardContent>
            <Stack direction="row" justifyContent="space-between" gap={1}>
              <Box minWidth={0}>
                <Typography
                  variant="subtitle2"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {skill.name}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {skill.description}
                </Typography>
                <Chip
                  size="small"
                  label={`Revision ${skill.revision} · ${skill.status}`}
                  sx={{ mt: 1 }}
                />
              </Box>
              <Button
                onClick={() => void inspect(skill)}
                disabled={busy || Boolean(draft)}
              >
                Read skill
              </Button>
            </Stack>
          </CardContent>
        </Card>
      ))}
      {draft ? (
        <Card
          component="section"
          aria-label={
            draft.mode === "create" ? "Create workspace skill" : "Skill details"
          }
          variant="outlined"
        >
          <CardContent>
            <Stack spacing={2}>
              <Typography variant="subtitle1">
                {draft.mode === "create"
                  ? "Create workspace skill"
                  : `Revision ${draft.revision}`}
              </Typography>
              <TextField
                label="Skill name"
                value={draft.name}
                disabled={!editable || busy || Boolean(draft.pendingReadback)}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              <TextField
                label="Skill description"
                multiline
                minRows={2}
                value={draft.description}
                disabled={!editable || busy || Boolean(draft.pendingReadback)}
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
              />
              <TextField
                label="Skill instructions"
                multiline
                minRows={7}
                value={draft.instructions}
                disabled={!editable || busy || Boolean(draft.pendingReadback)}
                onChange={(e) =>
                  setDraft({ ...draft, instructions: e.target.value })
                }
                helperText="Up to 16,000 UTF-8 bytes. This library stores instructions, not executable scripts or secret values."
              />
              {draft.digest ? (
                <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                  {draft.digest}
                </Typography>
              ) : null}
              {inputError(draft) ? (
                <Typography variant="caption" color="error">
                  {inputError(draft)}
                </Typography>
              ) : null}
              <Stack direction="row" spacing={1}>
                <Button onClick={() => setDraft(null)} disabled={busy}>
                  Close
                </Button>
                {editable ? (
                  <Button
                    variant="contained"
                    onClick={() => void save()}
                    disabled={busy || Boolean(inputError(draft))}
                  >
                    {draft.pendingReadback
                      ? "Verify saved skill"
                      : draft.mode === "create"
                        ? "Save skill"
                        : "Save new revision"}
                  </Button>
                ) : null}
                {editable && draft.mode !== "create" ? (
                  <Button
                    color="error"
                    onClick={() => void archive()}
                    disabled={busy}
                  >
                    Archive skill
                  </Button>
                ) : null}
              </Stack>
            </Stack>
          </CardContent>
        </Card>
      ) : null}
    </Stack>
  );
}
SkillLibraryPanel.propTypes = { client: PropTypes.object.isRequired };
