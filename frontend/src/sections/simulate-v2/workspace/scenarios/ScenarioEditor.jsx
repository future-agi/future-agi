import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  IconButton,
  Button,
  TextField,
  Slider,
  Tooltip,
  MenuItem,
  Select,
  InputLabel,
  FormControl,
  ToggleButton,
  ToggleButtonGroup,
  Switch,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { emitBuilderPrompt } from "../../_mock/builderPromptBus";
import { editBumpOf, bumpNote } from "../../_mock/scenarioEdits";
import { EDIT_EFFECT } from "../../_mock/decoupled";
import PersonaDrawer from "src/sections/persona/PersonaDrawer";
import PersonaCreateEditDrawer from "src/sections/persona/PersonaCreateEdit/PersonaCreateEditDrawer";
import {
  callSettingsLine,
  libraryPrefillFrom,
  personaOrigin,
  personaSwapPatch,
  personaTypeFor,
  usesPersonaLibrary,
} from "../../_mock/personaScenarios";
import { subGoalsOfScenario } from "../../_mock/envModel";
import {
  SUBGOAL_MAX,
  SUBGOAL_MIN,
  subGoalCountNote,
  subGoalUsageOf,
} from "../../_mock/scenarioSubGoals";
import { MarkToggle } from "./ScenarioStateBits";
import BlockerFlag from "./BlockerFlag";
import { setBlocker } from "../../_mock/releaseBlocker";
import { clickable, focusRing } from "../../components/clickable";

/**
 * Edit one scenario.
 *
 * Per engineering: every generated scenario is *pre-verified* — the world is
 * set up, a known-correct run has to pass, and the checks have to fail when
 * nothing is done. That verification pins the *task*, the *setup* and the
 * *checks* to a proof. If those get free-text edited afterwards the checks no
 * longer apply and nothing warns you.
 *
 * So this drawer only exposes what's *safe to edit directly*:
 *
 *   Universal (every agent type)
 *     · Name
 *     · Use case
 *     · Branch (what makes this one different from its siblings)
 *     · Passes when
 *     · Sub-goals — 2–7, each critical or supporting. Changing them changes
 *       what a pass means, so the row can't be graded until re-proved.
 *     · Must-pass — a mark on the set
 *
 * Nothing is rewritten in place (PRD v7 §4.10.3). The footer states, before
 * Save, the version the edit makes and what that does to past results:
 * patch (cosmetic — reused), minor (a grader fix — re-graded) or major (what
 * the caller does — re-run). A grader edit asks whether the intent stayed
 * the same. The must-pass mark moves the set, not the scenario.
 *
 *   Voice / chat only
 *     · Caller — tone, style, accent, language
 *     · Call constraints — max turns, background noise
 *
 * Everything else — what the caller actually wants, the world setup, the
 * checks that grade the run — moves through the builder chat, which can
 * rewrite it *and* re-run the pre-verification, so a change either works or
 * gets rejected loudly.
 */

const CONVERSATIONAL_SURFACES = [
  "voice",
  "chat",
  "messaging",
  "email",
  "multi",
];
const VOICE_ONLY_SURFACES = ["voice", "multi"];

const TONE_OPTIONS = [
  "neutral",
  "polite",
  "urgent",
  "impatient",
  "sceptical",
  "angry",
  "confused",
  "apologetic",
];
const STYLE_OPTIONS = [
  "concise",
  "verbose",
  "formal",
  "casual",
  "chatty",
  "terse",
];
const ACCENT_OPTIONS = ["US", "UK", "IN", "BR", "AE", "JP", "other"];
const LANGUAGE_OPTIONS = [
  "English",
  "Spanish",
  "Portuguese",
  "Hindi",
  "Japanese",
  "Arabic",
];
const NOISE_OPTIONS = ["none", "low", "high"];

/** Read "US female" / "IN male" → accent + gender fallback. */
const parseVoice = (voice) => {
  const s = (voice || "").toLowerCase();
  const accent =
    ACCENT_OPTIONS.find((a) => s.startsWith(a.toLowerCase())) || "US";
  return { accent };
};

/**
 * Turn the persona + variant hints into the caller-meta fields the
 * eng lead flagged as directly editable. Persona traits like
 * "impatient", "polite" map to tone; verbosity heuristics to style.
 */
const deriveCaller = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  const tone =
    TONE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) || "neutral";
  const style =
    STYLE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) ||
    (traits.includes("terse") ? "terse" : "casual");
  const { accent } = parseVoice(persona?.voice);
  return { tone, style, accent, language: "English" };
};

/** Persona trait "background noise" → high, else none. */
const deriveNoise = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  return traits.some((t) => t.includes("noise")) ? "high" : "none";
};

export default function ScenarioEditor({
  open,
  onClose,
  row,
  env,
  envState,
  onSave,
  onBuilderPrompt,
}) {
  const [draft, setDraft] = useState(row || {});
  /* The draft as it opened, derived defaults included — what "changed" is
     measured against. */
  const [initial, setInitial] = useState(row || {});
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [savingToLibrary, setSavingToLibrary] = useState(false);
  /* What the last library swap did — said once under the persona row. */
  const [swapNote, setSwapNote] = useState(null);
  /* A grader edit: the same intent (a fix → minor) or a different test (major). */
  const [intent, setIntent] = useState("same");

  useEffect(() => {
    if (!row) return;
    /*
      Populate the direct-edit fields the mock doesn't carry yet, so
      the drawer opens with defaults derived from the row rather than
      empty controls. If the mock ever starts storing them explicitly,
      those win.
    */
    const opened = {
      ...row,
      caller: row.caller || deriveCaller(row.persona),
      backgroundNoise: row.backgroundNoise || deriveNoise(row.persona),
    };
    setDraft(opened);
    setInitial(opened);
    setSwapNote(null);
    setIntent("same");
  }, [row]);

  if (!row) return null;

  const set = (k) => (v) => setDraft((d) => ({ ...d, [k]: v }));
  const setCaller = (k) => (v) =>
    setDraft((d) => ({
      ...d,
      caller: { ...(d.caller || {}), [k]: v },
      callerEdited: true,
    }));
  /* Editing a library persona here makes it this scenario's own version —
     it stays linked, but says it's been edited. */
  const setPersona = (k) => (v) =>
    setDraft((d) => ({
      ...d,
      persona: {
        ...(d.persona || {}),
        [k]: v,
        ...(d.persona?.libraryId != null && { edited: true }),
      },
    }));
  const origin = personaOrigin(draft.persona);
  const canUseLibrary = usesPersonaLibrary(env);

  const isConversational = CONVERSATIONAL_SURFACES.includes(env?.surface);
  const isVoice = VOICE_ONLY_SURFACES.includes(env?.surface);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  /* What the edit does to the scenario's version — stated before Save. */
  const bump = editBumpOf(initial, draft, { intent });
  /* Only the set's mark moved (must-pass): the row as it was plus the mark,
     rather than the derived defaults as an edit. The proof stands. */
  const metaOnly = !bump.level;
  /* Must-pass is a mark on the set — set through setBlocker so turning it
     back drops the override. */
  const toggleMustPass = (on) => setDraft((d) => setBlocker(d, on));
  const save = () => {
    const mustPassMoved = !!draft.critical !== !!initial.critical;
    const meta = mustPassMoved
      ? setBlocker({ ...row }, !!draft.critical)
      : { ...row };
    onSave(metaOnly ? meta : draft, { metaOnly, bump });
    onClose();
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              className="blocker-host"
            >
              <Typography sx={{ typography: "m2", fontWeight: 600 }}>
                Edit scenario
              </Typography>
              <BlockerFlag
                row={draft}
                size={15}
                onToggle={() => toggleMustPass(!draft.critical)}
              />
            </Stack>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              Changes apply to this environment&apos;s copy, not the pack it
              came from
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          {/* ─── Directly editable ─── */}
          <SectionHeader
            title="Directly editable"
            hint="These don't affect verification, so they save directly — as a new version."
          />

          <TextField
            size="small"
            label="Name"
            value={draft.name || ""}
            onChange={(e) => set("name")(e.target.value)}
            helperText="Kebab-case identifier for this scenario — e.g. polite-senior-verify-identity."
            InputProps={{
              sx: {
                typography: "s2",
                fontFamily: "ui-monospace, Menlo, monospace",
              },
            }}
          />
          <TextField
            size="small"
            label="Use case"
            value={draft.useCase || ""}
            disabled
            helperText="Read-only — the use case comes from the scenario pack and groups this row with its siblings. Move to a different pack to change it."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <TextField
            size="small"
            label="Branch"
            value={draft.branchCategory || ""}
            disabled
            helperText="Read-only — the branch identifies which sibling this row is inside its use case. Move to a different pack to change it."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <TextField
            size="small"
            label="Passes when"
            multiline
            minRows={2}
            value={draft.expected || ""}
            onChange={(e) => set("expected")(e.target.value)}
            helperText="What a pass looks like. Evals grade against this."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <SetMarks row={draft} onMustPass={toggleMustPass} />

          {/* ─── Sub-goals ─── */}
          <SectionHeader
            title="Sub-goals"
            hint={`${SUBGOAL_MIN}–${SUBGOAL_MAX} per scenario. It passes when every critical one holds; supporting ones are only reported.`}
          />
          <SubGoalsEditor
            draft={draft}
            setDraft={setDraft}
            env={env}
            envState={envState}
          />

          {/* ─── Persona ─── */}
          {/*
            The persona is the who — the character on the other end of
            the run. Previously only editable on the standalone
            Personas tab, which has been folded into scenarios. Name,
            age group, voice and traits are all directly-editable
            because they shape the run's surface behaviour, not the
            underlying verified check. The persona lives ON the
            scenario (`row.persona`), so writes here go straight to
            the row.
          */}
          <SectionHeader
            title="Persona"
            hint="Who's on the other end of the run. Shapes tone, pacing and any traits the caller carries into the conversation."
          />
          {/* Where this caller came from, and the way to and from the workspace
              persona library (Environments → Personas). Callers only — a
              browser or API agent has no one on the other end. */}
          {canUseLibrary && (
            <Stack spacing={0.75}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                flexWrap="wrap"
                rowGap={1}
              >
                {origin && (
                  <Box
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.5,
                      px: 0.875,
                      height: 24,
                      borderRadius: "4px",
                      border: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Iconify
                      icon={
                        origin.kind === "generated"
                          ? "solar:magic-stick-3-linear"
                          : "solar:users-group-rounded-linear"
                      }
                      width={14}
                      sx={{ color: "text.tertiary" }}
                    />
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.secondary",
                      }}
                    >
                      {origin.label}
                    </Typography>
                  </Box>
                )}
                <Box flex={1} />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setLibraryOpen(true)}
                  startIcon={
                    <Iconify
                      icon="solar:users-group-rounded-linear"
                      width={16}
                    />
                  }
                  sx={{
                    color: "text.primary",
                    borderColor: "divider",
                    flexShrink: 0,
                  }}
                >
                  {draft.persona
                    ? "Swap for a library persona"
                    : "Use a library persona"}
                </Button>
                {draft.persona && origin?.kind !== "library" && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => setSavingToLibrary(true)}
                    startIcon={
                      <Iconify icon="solar:bookmark-linear" width={16} />
                    }
                    sx={{
                      color: "text.primary",
                      borderColor: "divider",
                      flexShrink: 0,
                    }}
                  >
                    Save to library
                  </Button>
                )}
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {swapNote ||
                  (origin?.kind === "library"
                    ? "A copy of the library persona — edits below change this scenario only, and later library edits don't reach it."
                    : origin?.kind === "edited"
                      ? "Save to library keeps this version as a new persona your other environments can use."
                      : "The builder wrote this caller for this scenario. Save to library to reuse it in other environments.")}
              </Typography>
            </Stack>
          )}
          <TextField
            size="small"
            label="Name"
            value={draft.persona?.name || ""}
            onChange={(e) => setPersona("name")(e.target.value)}
            helperText="Display name for the caller — e.g. The Polite Senior Caller."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              size="small"
              label="Age / age group"
              value={draft.persona?.age || draft.persona?.ageGroup || ""}
              onChange={(e) => setPersona("age")(e.target.value)}
              helperText="Range or number — e.g. 60-70."
              InputProps={{ sx: { typography: "s2" } }}
              fullWidth
            />
            <TextField
              size="small"
              label="Voice"
              value={draft.persona?.voice || ""}
              onChange={(e) => setPersona("voice")(e.target.value)}
              helperText="Accent + gender — e.g. US female."
              InputProps={{ sx: { typography: "s2" } }}
              fullWidth
            />
          </Stack>
          <TextField
            size="small"
            label="Traits"
            value={
              Array.isArray(draft.persona?.traits)
                ? draft.persona.traits.join(", ")
                : draft.persona?.traits || ""
            }
            onChange={(e) =>
              setPersona("traits")(
                e.target.value
                  .split(",")
                  .map((t) => t.trim())
                  .filter(Boolean),
              )
            }
            helperText="Comma-separated traits the caller carries in — e.g. polite, elderly, hard of hearing."
            InputProps={{ sx: { typography: "s2" } }}
          />

          {/* ─── Caller (voice / chat only) ─── */}
          {isConversational && (
            <>
              <SectionHeader
                title="Caller"
                hint={
                  draft.callerFrom
                    ? draft.callerEdited
                      ? `Adjusted from ${draft.callerFrom}'s defaults for this scenario.`
                      : `Set from ${draft.callerFrom}'s personality and library settings — change any to override.`
                    : "How the caller comes across in the run. Voice/chat surfaces only — a browser or coding agent has no caller."
                }
              />
              {!isVoice && callSettingsLine(draft.persona?.callSettings) && (
                <LibrarySettingsNote
                  name={draft.persona.name}
                  line={callSettingsLine(draft.persona.callSettings)}
                />
              )}
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Tone</InputLabel>
                  <Select
                    label="Tone"
                    value={draft.caller?.tone || "neutral"}
                    onChange={(e) => setCaller("tone")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {TONE_OPTIONS.map((o) => (
                      <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
                        {o}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Style</InputLabel>
                  <Select
                    label="Style"
                    value={draft.caller?.style || "casual"}
                    onChange={(e) => setCaller("style")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {STYLE_OPTIONS.map((o) => (
                      <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
                        {o}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Accent</InputLabel>
                  <Select
                    label="Accent"
                    value={draft.caller?.accent || "US"}
                    onChange={(e) => setCaller("accent")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {ACCENT_OPTIONS.map((o) => (
                      <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
                        {o}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Language</InputLabel>
                  <Select
                    label="Language"
                    value={draft.caller?.language || "English"}
                    onChange={(e) => setCaller("language")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {LANGUAGE_OPTIONS.map((o) => (
                      <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
                        {o}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Stack>
            </>
          )}

          {/* ─── Call constraints (voice only) ─── */}
          {isVoice && (
            <>
              <SectionHeader
                title="Call constraints"
                hint="Voice-agent specific. Every generated scenario carries defaults; overriding them here is safe."
              />
              {callSettingsLine(draft.persona?.callSettings, {
                voice: true,
              }) && (
                <LibrarySettingsNote
                  name={draft.persona.name}
                  line={callSettingsLine(draft.persona.callSettings, {
                    voice: true,
                  })}
                />
              )}
              <Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
                  >
                    Max turns
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    ~{draft.turns || 0}
                  </Typography>
                </Stack>
                <Slider
                  size="small"
                  min={2}
                  max={20}
                  value={draft.turns || 0}
                  onChange={(_, v) =>
                    setDraft((d) => ({ ...d, turns: v, maxTurns: v }))
                  }
                />
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  How long the run is allowed to go before it&apos;s called off.
                </Typography>
              </Box>

              <Box>
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}
                >
                  Background noise
                </Typography>
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={draft.backgroundNoise || "none"}
                  onChange={(_, v) => v && set("backgroundNoise")(v)}
                >
                  {NOISE_OPTIONS.map((n) => (
                    <ToggleButton key={n} value={n}>
                      {n}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Box>
            </>
          )}

          {/* ─── Turn limit (chat and other text surfaces) ─── */}
          {isConversational && !isVoice && (
            <>
              <SectionHeader
                title="Conversation limit"
                hint="A chat that goes on past this is called off and graded on where it got to. Overriding the default here is safe."
              />
              <Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
                  >
                    Max turns
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {draft.maxTurns || draft.turns || 10}
                  </Typography>
                </Stack>
                <Slider
                  size="small"
                  min={2}
                  max={40}
                  value={draft.maxTurns || draft.turns || 10}
                  onChange={(_, v) =>
                    setDraft((d) => ({ ...d, turns: v, maxTurns: v }))
                  }
                />
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Caller and agent messages each count as a turn.
                </Typography>
              </Box>
            </>
          )}

          {/* ─── Twin seed override (twin-backed envs only) ─── */}
          {envState?.twinBacking?.services?.length > 0 && (
            <>
              <SectionHeader
                title="Starting state override"
                hint="Optional. This scenario's own starting state; blank uses the environment's default."
              />
              <TextField
                size="small"
                multiline
                minRows={4}
                label="Starting state, in words"
                value={draft.twinSeedPrompt || ""}
                onChange={(e) => set("twinSeedPrompt")(e.target.value)}
                placeholder="e.g. Slack #support has 3 messages from the same angry customer; the last one asks to escalate."
                helperText="We turn this into the world's starting state at run time. Each run starts from a fresh copy."
                InputProps={{ sx: { typography: "s2" } }}
              />
            </>
          )}

          {/* ─── Edit via the builder ─── */}
          <SectionHeader
            title="Edit through the builder"
            hint="Locked to the verification proof — ask the builder on the left to change them."
          />

          {/* Clicking any of these hands the suggestion to the builder
              chat on the left via `builderPromptBus` — a module-level
              pub/sub the workspace subscribes to on mount. No prop
              drilling, no window events, just an in-process emit. */}
          {(() => {
            const send = (text) => {
              emitBuilderPrompt(text);
              onBuilderPrompt?.(text);
              onClose();
            };
            return (
              <>
                <LockedRow
                  label="What the caller wants"
                  value={draft.task}
                  prompt={`change what the caller wants in ${draft.name} to …`}
                  onSend={send}
                />
                <LockedRow
                  label="Setup / world state"
                  value={draft.setup || "seeded from the environment"}
                  prompt={`change the setup for ${draft.name} to …`}
                  onSend={send}
                />
                <LockedRow
                  label="What we measure (checks)"
                  value={
                    draft.checks?.length
                      ? draft.checks.map((c) => c.label || c.id).join(" · ")
                      : "graded against the passes-when line + the derived sub-goals"
                  }
                  prompt={`change the checks on ${draft.name} to …`}
                  onSend={send}
                />
              </>
            );
          })()}

          <Stack
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            sx={{
              p: 1.75,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "status.discovery.border",
              bgcolor: "status.discovery.bg",
            }}
          >
            <Iconify
              icon="solar:shield-check-linear"
              width={16}
              sx={{
                color: "primary.main",
                flexShrink: 0,
                mt: "1px",
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Scenarios are verified before they land, so edits go through the
              builder, which re-checks on save.
            </Typography>
          </Stack>
        </Stack>

        {dirty && (
          <BumpPreview bump={bump} intent={intent} onIntent={setIntent} />
        )}
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            disabled={!dirty}
            onClick={save}
          >
            Save scenario
          </Button>
        </Stack>
      </Stack>
      <PersonaDrawer
        open={libraryOpen}
        onClose={() => setLibraryOpen(false)}
        personaCreateEditType={personaTypeFor(env)}
        title="Use a library persona"
        subtitle="Pick the caller for this scenario. Edits you make after this change this scenario only."
        addLabel="Use persona"
        single
        current={
          draft.persona
            ? {
                libraryId: draft.persona.libraryId ?? null,
                name: draft.persona.name,
              }
            : null
        }
        onAddPersonas={(picked) => {
          setLibraryOpen(false);
          if (!picked?.length) return;
          /* One caller per scenario — the pick replaces the current one, and
             anything written in the old caller's voice is rewritten in the
             new one's, so nothing still speaks for the persona it replaced. */
          const { rewrote, ...patch } = personaSwapPatch(draft, picked[0]);
          setDraft((d) => ({ ...d, ...patch, callerEdited: false }));
          setSwapNote(
            rewrote
              ? `Now played by ${picked[0].name} — brief, opening line and caller settings rewritten in their voice.`
              : `Now played by ${picked[0].name} — caller settings updated. This scenario scripts its own pressure, so its brief is kept.`,
          );
        }}
      />
      {/* Save this scenario's caller as a new library persona, then link the
          scenario to it. */}
      <PersonaCreateEditDrawer
        open={savingToLibrary}
        onClose={() => setSavingToLibrary(false)}
        personaCreateEditType={personaTypeFor(env) || "voice"}
        updatePersonaType={() => {}}
        prefill={libraryPrefillFrom(draft.persona, draft)}
        onSaved={(saved) => {
          setDraft((d) => ({
            ...d,
            persona: {
              ...(d.persona || {}),
              libraryId: saved.id,
              libraryName: saved.name,
              name: saved.name,
              edited: false,
            },
          }));
          setSwapNote(
            `Saved to the persona library as ${saved.name} — this scenario now uses it.`,
          );
        }}
      />
    </SideDrawer>
  );
}

ScenarioEditor.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  row: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  onSave: PropTypes.func,
  onBuilderPrompt: PropTypes.func,
};

/*
  What this edit does to past results, read before the click that makes it
  true. A grader edit asks the one question
  the fields can't answer: same intent, or a different test?
*/
function BumpPreview({ bump, intent, onIntent }) {
  return (
    <Box
      sx={{
        mx: 2.5,
        mb: 1.5,
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
        flexShrink: 0,
      }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {bump.level ? EDIT_EFFECT[bump.level] : "Past results still compare"}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
        {bump.why ? `${bump.why}. ` : ""}
        {bumpNote(bump.level)}
      </Typography>
      {bump.graderEdit && !bump.rewrite && !bump.fields.caller.length && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Its checks changed —
          </Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={intent}
            onChange={(_, v) => v && onIntent(v)}
          >
            <ToggleButton value="same">Same intent (a fix)</ToggleButton>
            <ToggleButton value="changed">
              It tests something else now
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>
      )}
    </Box>
  );
}
BumpPreview.propTypes = {
  bump: PropTypes.object.isRequired,
  intent: PropTypes.string,
  onIntent: PropTypes.func,
};

/*
  The must-pass mark — on the set, not the scenario: no scenario bump, no
  re-proof.
*/
function SetMarks({ row, onMustPass }) {
  if (!onMustPass) return null;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={1.25}
      sx={{
        p: 1.5,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon={
          row.critical ? "solar:shield-check-bold" : "solar:shield-check-linear"
        }
        width={16}
        sx={{
          color: row.critical ? "text.primary" : "text.disabled",
          mt: "2px",
          flexShrink: 0,
        }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Must-pass
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          An improvement that breaks it is rejected, however much else it lifts.
        </Typography>
      </Box>
      <Switch
        size="small"
        checked={!!row.critical}
        onChange={(e) => onMustPass(e.target.checked)}
        inputProps={{ "aria-label": "Must-pass" }}
        sx={{ flexShrink: 0, mt: "-2px" }}
      />
    </Stack>
  );
}
SetMarks.propTypes = {
  row: PropTypes.object.isRequired,
  onMustPass: PropTypes.func,
};

/* A library persona's own call settings, said where they apply. */
function LibrarySettingsNote({ name, line }) {
  return (
    <Stack
      direction="row"
      spacing={0.75}
      alignItems="flex-start"
      sx={{
        px: 1.25,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon="solar:users-group-rounded-linear"
        width={14}
        sx={{ color: "text.tertiary", mt: 0.25, flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        <Box
          component="span"
          sx={{ fontWeight: 600, color: "text.primary" }}
        >{`From ${name}: `}</Box>
        {line}
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {" "}
          — set in the persona library.
        </Box>
      </Typography>
    </Stack>
  );
}
LibrarySettingsNote.propTypes = {
  name: PropTypes.string,
  line: PropTypes.string,
};

/*
  The scenario's own sub-goals. The scenario owns which it has and how much
  each matters; the catalogue owns what each checks — so each says how many
  scenarios share its definition. The rule the scenario tests is always one
  of them, read from the contract, so it can be re-marked but not removed.
*/
const goalText = (g) => (typeof g === "string" ? g : g?.text || g?.label || "");

function SubGoalsEditor({ draft, setDraft, env, envState }) {
  const [adding, setAdding] = useState("");
  const usage = useMemo(() => subGoalUsageOf(env, envState), [env, envState]);
  const base =
    draft.subGoals?.length || !draft.subTasks?.length
      ? draft
      : { ...draft, subGoals: draft.subTasks.map(goalText) };
  const goals = subGoalsOfScenario(base, env, envState);
  const own = goals.filter((g) => !g.key.endsWith(":rule"));
  const note = subGoalCountNote(goals.length);
  const write = (texts, marks) =>
    setDraft((d) => ({
      ...d,
      subGoals: texts,
      subGoalMarks: { ...(d.subGoalMarks || {}), ...marks },
      subGoalsUnproved: true,
    }));
  const add = () => {
    const text = adding.trim();
    if (!text || goals.length >= SUBGOAL_MAX) return;
    write([...own.map((g) => g.text), text], {});
    setAdding("");
  };
  return (
    <Stack spacing={0.75}>
      {goals.map((g, i) => {
        const used = usage.get(g.id)?.usedBy || 0;
        const isRule = g.key.endsWith(":rule");
        return (
          <Stack
            key={g.key}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.25,
              py: 0.75,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.disabled",
                width: 14,
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {i + 1}.
            </Typography>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2" }}>{g.text}</Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {[
                  isRule && "from the rule it tests",
                  used > 1 ? `shared · used by ${used}` : "only this scenario",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
            <MarkToggle
              value={g.mark}
              onChange={(m) =>
                write(
                  own.map((x) => x.text),
                  { [g.id]: m },
                )
              }
            />
            <Tooltip
              arrow
              title={
                isRule
                  ? "Comes from the rule it tests"
                  : goals.length <= SUBGOAL_MIN
                    ? `Keep at least ${SUBGOAL_MIN}`
                    : "Remove"
              }
            >
              <span>
                <IconButton
                  size="small"
                  disabled={isRule || goals.length <= SUBGOAL_MIN}
                  onClick={() =>
                    write(
                      own.filter((x) => x.key !== g.key).map((x) => x.text),
                      {},
                    )
                  }
                >
                  <Iconify
                    icon="solar:close-circle-linear"
                    width={16}
                    sx={{ color: "text.tertiary" }}
                  />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        );
      })}
      <Stack direction="row" spacing={1} alignItems="center">
        <TextField
          size="small"
          fullWidth
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          disabled={goals.length >= SUBGOAL_MAX}
          placeholder={
            goals.length >= SUBGOAL_MAX
              ? `${SUBGOAL_MAX} is the most a scenario takes`
              : "Add a sub-goal — e.g. Confirm the refund amount before issuing it"
          }
          InputProps={{ sx: { typography: "s2" } }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={add}
          disabled={!adding.trim() || goals.length >= SUBGOAL_MAX}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Add
        </Button>
      </Stack>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {note}
        </Typography>
      )}
      {draft.subGoalsUnproved && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Can&apos;t be graded until re-proved — re-prove it from its state once
          saved.
        </Typography>
      )}
    </Stack>
  );
}
SubGoalsEditor.propTypes = {
  draft: PropTypes.object,
  setDraft: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
};

function SectionHeader({ title, hint }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.primary",
          textTransform: "none",
          letterSpacing: 0.6,
          mb: 0.375,
        }}
      >
        {title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {hint}
      </Typography>
    </Box>
  );
}
SectionHeader.propTypes = { title: PropTypes.string, hint: PropTypes.string };

/**
 * A "locked" field the user can't edit inline: shows the current value
 * and a copy-friendly example prompt to paste into the builder on the
 * left. Doesn't try to open the builder itself — the builder is
 * always visible in the workspace, so the affordance is "copy this
 * and send it".
 */
function LockedRow({ label, value, prompt, onSend }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Iconify
          icon="solar:lock-keyhole-minimalistic-linear"
          width={14}
          sx={{ color: "text.tertiary", mt: "3px", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              mb: 0.375,
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            {label}
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              color: "text.primary",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {value || "—"}
          </Typography>
          {/*
            The suggestion reads as a filled purple pill so it's obviously
            a button — clicking it sends the prompt into the builder chat
            on the left. The whole card is still clickable, but the pill
            names the exact action.
          */}
          {/* Suggestion pill is the click target — subtle at rest, its
              own hover state, and a "Send to builder" title on hover so
              the reader knows what will happen. */}
          {onSend ? (
            <Tooltip
              arrow
              placement="top"
              title="Send this to the builder chat"
            >
              <Box
                {...clickable((e) => {
                  e.stopPropagation();
                  onSend(prompt);
                })}
                sx={{
                  ...focusRing,
                  mt: 1,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.75,
                  px: 1,
                  py: 0.5,
                  borderRadius: 1,
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: "divider",
                  color: "text.secondary",
                  bgcolor: "background.paper",
                  "&:hover, &:focus-visible": {
                    borderColor: "text.primary",
                    color: "text.primary",
                    outline: "none",
                  },
                  transition: "border-color .12s ease, color .12s ease",
                  maxWidth: "100%",
                }}
              >
                <Iconify
                  icon="solar:chat-round-line-linear"
                  width={12}
                  sx={{ color: "inherit", flexShrink: 0, opacity: 0.7 }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    color: "inherit",
                    minWidth: 0,
                  }}
                >
                  {prompt}
                </Typography>
                <Iconify
                  icon="solar:arrow-right-linear"
                  width={12}
                  sx={{ color: "inherit", flexShrink: 0, opacity: 0.6 }}
                />
              </Box>
            </Tooltip>
          ) : (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 1 }}
            >
              <Iconify
                icon="solar:chat-round-line-linear"
                width={14}
                sx={{ color: "text.tertiary" }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "text.tertiary",
                }}
              >
                &ldquo;{prompt}&rdquo;
              </Typography>
            </Stack>
          )}
        </Box>
      </Stack>
    </Box>
  );
}
LockedRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  prompt: PropTypes.string,
  onSend: PropTypes.func,
};
