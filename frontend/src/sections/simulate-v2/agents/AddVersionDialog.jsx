import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { agentSourceOf } from "../_mock/agentRegistry";
import { getEnvironment } from "../_mock/environments";
import { nextMajorLabel } from "../_mock/versions";
import { rulesOf, subGoalCatalogOf } from "../_mock/envModel";
import {
  raiseNudgesIn,
  dispatchNudges,
  nudgeAnnouncement,
  likelyFixLine,
  openNudgesIn,
} from "../_mock/nudgeEffects";
import { useSimStore } from "../store";
import { MONO } from "./agentViewUtils";
import { clickable, focusRing } from "../components/clickable";

const AREAS = [
  { kind: "instructions", label: "Instructions" },
  { kind: "model", label: "Model" },
  { kind: "tool", label: "Tools" },
  { kind: "voice", label: "Voice" },
  { kind: "knowledge", label: "Knowledge" },
];

const lines = (t) =>
  String(t || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

/**
 * A new version for an agent we can't re-read. What it takes follows the
 * source: an upload takes the new bundle, a pasted prompt is edited here,
 * an endpoint gets a note on what changed. Only what the source can show is
 * offered, and each change says what it changed to, so Files and Compare
 * show it. Environments following production move to it; pinned ones stay put.
 *
 * Saving makes an agent version and nothing else (PRD v7 §4.10.1). What no
 * longer fits in the environments that use the agent is raised there as
 * nudges; the note, if it names a rule, pre-fills the fix-or-policy answer.
 */
export default function AddVersionDialog({
  open,
  onClose,
  agent,
  definition,
  baseTools,
  onSave,
}) {
  const source = useMemo(() => agentSourceOf(agent), [agent]);
  const isUpload = source.kind === "upload";
  /* A built-in agent is its prompt too — a new version edits it here. */
  const isPrompt = source.kind === "prompt" || source.kind === "builtin";
  const currentPrompt = definition?.instructions?.text || "";
  const currentModel = definition?.model?.visible
    ? definition.model.name
    : null;
  const currentVoice = definition?.voice?.visible
    ? definition.voice.voice
    : null;
  const tools = baseTools || definition?.tools?.items || [];

  const [areas, setAreas] = useState([]);
  const [note, setNote] = useState("");
  const [files, setFiles] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [voice, setVoice] = useState("");
  const [toolOp, setToolOp] = useState("added");
  const [toolName, setToolName] = useState("");
  const { state, dispatch } = useSimStore();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  /* Every environment testing this agent — each has its own contract. */
  const envs = useMemo(
    () =>
      (agent?.usedIn || [])
        .map(
          (u) =>
            state.myEnvironments?.find((e) => e.id === u.envId) ||
            getEnvironment(u.envId),
        )
        .filter(Boolean),
    [agent, state.myEnvironments],
  );
  /* The example names a rule its environments test, so a note written like
     it is read as touching that rule — and asks fix or spec change. */
  const exampleNote = useMemo(() => {
    const tested = envs
      .flatMap((e) => {
        const es = state.byEnv?.[e.id];
        return es
          ? subGoalCatalogOf(e, es)
              .filter((g) => g.tests.kind === "rule")
              .map((g) => rulesOf(e, es).find((r) => r.id === g.tests.ref))
          : [];
      })
      .find(Boolean);
    return tested
      ? `Policy change: no longer holds “${tested.text}”`
      : "Escalates refunds above $200 to a human";
  }, [envs, state.byEnv]);
  useEffect(() => {
    if (!open) return;
    setAreas([]);
    setNote("");
    setFiles([]);
    setPrompt(currentPrompt);
    setModel("");
    setVoice("");
    setToolOp("added");
    setToolName("");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Only what this source exposes — a pasted prompt is edited directly. */
  const offered = AREAS.filter((a) =>
    a.kind === "model"
      ? !!currentModel
      : a.kind === "voice"
        ? !!currentVoice
        : a.kind === "instructions"
          ? !isPrompt
          : true,
  );
  const next = nextMajorLabel(agent.versions.map((v) => v.label));
  const toggle = (k) =>
    setAreas((xs) => (xs.includes(k) ? xs.filter((x) => x !== k) : [...xs, k]));
  const on = (k) => areas.includes(k);

  const before = lines(currentPrompt);
  const after = lines(prompt);
  const promptAdded = after.filter((l) => !before.includes(l));
  const promptRemoved = before.filter((l) => !after.includes(l));
  const promptChanged =
    isPrompt && (promptAdded.length > 0 || promptRemoved.length > 0);

  /* A ticked area that needs a name has one. */
  const complete =
    (!on("model") || model.trim()) &&
    (!on("voice") || voice.trim()) &&
    (!on("tool") || toolName.trim());
  const ready =
    complete &&
    (isUpload
      ? files.length > 0
      : areas.length > 0 || promptChanged || !!note.trim());

  const save = () => {
    const now = new Date().toISOString();
    const detail = note.trim() || "Recorded by hand";
    const tool = toolName.trim();
    const changes = [
      ...(promptChanged
        ? [
            {
              kind: "instructions",
              title: "Prompt edited",
              detail: [
                promptAdded.length &&
                  `${promptAdded.length} line${promptAdded.length === 1 ? "" : "s"} added`,
                promptRemoved.length && `${promptRemoved.length} removed`,
              ]
                .filter(Boolean)
                .join(", "),
              prompt,
              /* New lines land in the prompt Files and Compare show. */
              ...(promptAdded.length && {
                addendum: promptAdded
                  .map((l) => (l.startsWith("- ") ? l : `- ${l}`))
                  .join("\n"),
              }),
            },
          ]
        : []),
      ...areas.map((k) => {
        const label = AREAS.find((a) => a.kind === k).label;
        if (k === "model")
          return {
            kind: k,
            title: "Model changed",
            detail: `${currentModel} → ${model.trim()}`,
            to: model.trim(),
          };
        if (k === "voice")
          return {
            kind: k,
            title: "Voice changed",
            detail: `Now speaking as ${voice.trim()}`,
            voice: voice.trim(),
          };
        if (k === "tool") {
          return {
            kind: k,
            title: `Tool ${toolOp} · ${tool}`,
            detail,
            tool: {
              name: tool,
              desc: tools.find((t) => t.name === tool)?.desc || "",
              args: [],
            },
            ...(toolOp === "removed" && { removed: true }),
          };
        }
        return {
          kind: k,
          title: `${label} changed`,
          detail,
          /* An instructions change written down is part of what the agent now
             says — it lands in its prompt, so Files and Compare show it. */
          ...(k === "instructions" &&
            note.trim() && { addendum: `- ${note.trim()}` }),
        };
      }),
    ];
    /* Nothing ticked, but a note on how it behaves now — for an agent we
       can't see inside, that note is the change to its instructions. */
    if (!changes.length && note.trim() && !isUpload) {
      changes.push({
        kind: "instructions",
        title: "Behaviour changed",
        detail: note.trim(),
      });
    }
    const bundle = files.map((f) => f.name).join(" + ");
    const what = changes
      .map((c) => c.title.split(" · ")[0].toLowerCase())
      .join(", ");
    const record = {
      id: `agent-${next}`,
      label: next,
      note:
        note.trim() ||
        (isUpload
          ? `New bundle ${bundle}${what ? ` — ${what}` : ""}.`
          : `Changed: ${what}.`),
      createdAt: now,
      connectedAt: now,
      origin: "manual",
      basedOnVersion: agent.latest,
      changes,
      /* The optional one-line change note — pre-fills a rule question it names. */
      ...(note.trim() && { changeNote: note.trim() }),
      ...(isUpload && { bundle }),
      ...(on("tool") &&
        tool && {
          tools:
            toolOp === "removed"
              ? tools.filter((t) => t.name !== tool)
              : [
                  ...tools.filter((t) => t.name !== tool),
                  { name: tool, desc: "", args: [] },
                ],
        }),
    };
    onSave(record);
    /* An agent version only — E and S stay. What no longer fits becomes a
       nudge in each environment that uses the agent. */
    const results = raiseNudgesIn(state, envs, record);
    dispatchNudges(dispatch, results);
    const said = nudgeAnnouncement(results);
    const fix = results.flatMap((r) => r.likelyFixes)[0];
    if (said.count) {
      enqueueSnackbar(said.text, {
        variant: "info",
        autoHideDuration: 8000,
        action: () => (
          <Button
            size="small"
            onClick={() => openNudgesIn(navigate, said.envs[0].id)}
            sx={{ color: "inherit" }}
          >
            Review
          </Button>
        ),
      });
    } else if (fix) {
      enqueueSnackbar(likelyFixLine(fix), {
        variant: "info",
        autoHideDuration: 5000,
      });
    }
  };

  const intro = isUpload
    ? `Upload the new bundle — it's read once, like the first.`
    : isPrompt
      ? "Edit the prompt as it now stands."
      : source.kind === "endpoint"
        ? `We can't see inside ${agent.name}, so tell us what changed.`
        : `We can't sync ${agent.name}, so tell us what changed.`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: isPrompt ? 720 : 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 2 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "m3", fontWeight: 600 }}
          >{`Add version ${next}`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {`${intro} This saves an agent version only — environments following production test it on their next run.`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Stack>
      <Stack spacing={2} sx={{ px: 3, pb: 2.5 }}>
        {isUpload && (
          <Box
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (e.dataTransfer?.files?.length)
                setFiles([...e.dataTransfer.files]);
            }}
            sx={{
              border: "1px dashed",
              borderColor: "divider",
              borderRadius: "8px",
              bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
              p: 2.5,
              textAlign: "center",
            }}
          >
            <Iconify
              icon="solar:cloud-upload-linear"
              width={24}
              sx={{ color: "text.subtitle" }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600, mt: 0.75 }}>
              {files.length
                ? files.map((f) => f.name).join(" + ")
                : `Drop the new bundle — replaces ${source.where}`}
            </Typography>
            <Button
              component="label"
              size="small"
              variant="outlined"
              sx={{ mt: 1.25 }}
            >
              {files.length ? "Choose again" : "Choose files"}
              <input
                hidden
                multiple
                type="file"
                onChange={(e) => {
                  if (e.target.files?.length) setFiles([...e.target.files]);
                  e.target.value = "";
                }}
              />
            </Button>
          </Box>
        )}
        {isPrompt && (
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={6}
            maxRows={14}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            InputProps={{
              sx: { typography: "s2", fontFamily: MONO, fontSize: 13 },
            }}
          />
        )}
        <Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: 0.5,
              color: "text.subtitle",
              mb: 0.75,
            }}
          >
            {isUpload || isPrompt ? "What else changed" : "What changed"}
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
            {offered.map((a) => (
              <Box
                key={a.kind}
                {...clickable(() => toggle(a.kind), { pressed: on(a.kind) })}
                sx={{
                  px: 1.25,
                  height: 30,
                  display: "inline-flex",
                  alignItems: "center",
                  borderRadius: "4px",
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: on(a.kind) ? "text.primary" : "divider",
                  bgcolor: (t) =>
                    on(a.kind)
                      ? alpha(t.palette.text.primary, 0.06)
                      : "transparent",
                  ...focusRing,
                }}
              >
                <Typography
                  sx={{ typography: "s2", fontWeight: on(a.kind) ? 600 : 500 }}
                >
                  {a.label}
                </Typography>
              </Box>
            ))}
          </Stack>
        </Box>
        {on("model") && (
          <TextField
            size="small"
            fullWidth
            value={model}
            onChange={(e) => setModel(e.target.value)}
            label="New model"
            placeholder={`Was ${currentModel}`}
            InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
          />
        )}
        {on("voice") && (
          <TextField
            size="small"
            fullWidth
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            label="New voice"
            placeholder={`Was ${currentVoice}`}
            InputProps={{ sx: { typography: "s2" } }}
          />
        )}
        {on("tool") && (
          <Stack direction="row" spacing={1}>
            <Select
              size="small"
              value={toolOp}
              onChange={(e) => {
                setToolOp(e.target.value);
                setToolName("");
              }}
              sx={{ typography: "s2", width: 120, flexShrink: 0 }}
            >
              <MenuItem value="added" sx={{ typography: "s2" }}>
                Added
              </MenuItem>
              <MenuItem
                value="removed"
                disabled={!tools.length}
                sx={{ typography: "s2" }}
              >
                Removed
              </MenuItem>
            </Select>
            {toolOp === "removed" ? (
              <Select
                size="small"
                fullWidth
                displayEmpty
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
                renderValue={(v) =>
                  v || (
                    <Box component="span" sx={{ color: "text.disabled" }}>
                      Which tool
                    </Box>
                  )
                }
                sx={{ typography: "s2", fontFamily: MONO }}
              >
                {tools.map((t) => (
                  <MenuItem
                    key={t.name}
                    value={t.name}
                    sx={{ typography: "s2", fontFamily: MONO }}
                  >
                    {t.name}
                  </MenuItem>
                ))}
              </Select>
            ) : (
              <TextField
                size="small"
                fullWidth
                value={toolName}
                onChange={(e) =>
                  setToolName(e.target.value.replace(/\s+/g, "_"))
                }
                placeholder="Tool name, e.g. check_loyalty_status"
                InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
              />
            )}
          </Stack>
        )}
        <TextField
          size="small"
          fullWidth
          multiline
          minRows={isPrompt ? 1 : 3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={
            isPrompt || isUpload
              ? `Change note (optional) — e.g. ${exampleNote}`
              : `e.g. ${exampleNote}`
          }
          InputProps={{ sx: { typography: "s2" } }}
        />
      </Stack>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!ready}
          onClick={save}
        >{`Save as ${next}`}</Button>
      </Stack>
    </Dialog>
  );
}

AddVersionDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object.isRequired,
  onSave: PropTypes.func.isRequired,
  /* The latest version's definition — the prompt, model and voice it changes from. */
  definition: PropTypes.object,
  baseTools: PropTypes.array,
};
