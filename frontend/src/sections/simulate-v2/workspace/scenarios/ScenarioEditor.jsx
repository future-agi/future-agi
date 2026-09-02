import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, IconButton, Button, TextField, Slider,
  MenuItem, Select, InputLabel, FormControl, ToggleButton, ToggleButtonGroup,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";

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

const CONVERSATIONAL_SURFACES = ["voice", "chat", "messaging", "email", "multi"];
const VOICE_ONLY_SURFACES = ["voice", "multi"];

const TONE_OPTIONS = [
  "neutral", "polite", "urgent", "impatient", "sceptical",
  "angry", "confused", "apologetic",
];
const STYLE_OPTIONS = [
  "concise", "verbose", "formal", "casual", "chatty", "terse",
];
const ACCENT_OPTIONS = ["US", "UK", "IN", "BR", "AE", "JP", "other"];
const LANGUAGE_OPTIONS = ["English", "Spanish", "Portuguese", "Hindi", "Japanese", "Arabic"];
const NOISE_OPTIONS = ["none", "low", "high"];

/** Read "US female" / "IN male" → accent + gender fallback. */
const parseVoice = (voice) => {
  const s = (voice || "").toLowerCase();
  const accent = ACCENT_OPTIONS.find((a) => s.startsWith(a.toLowerCase())) || "US";
  return { accent };
};

/**
 * Turn the persona + variant hints into the caller-meta fields the
 * eng lead flagged as directly editable. Persona traits like
 * "impatient", "polite" map to tone; verbosity heuristics to style.
 */
const deriveCaller = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  const tone = TONE_OPTIONS.find((o) => traits.some((t) => t.includes(o))) || "neutral";
  const style = STYLE_OPTIONS.find((o) => traits.some((t) => t.includes(o)))
    || (traits.includes("terse") ? "terse" : "casual");
  const { accent } = parseVoice(persona?.voice);
  return { tone, style, accent, language: "English" };
};

/** Persona trait "background noise" → high, else none. */
const deriveNoise = (persona) => {
  const traits = (persona?.traits || []).map((t) => t.toLowerCase());
  return traits.some((t) => t.includes("noise")) ? "high" : "none";
};

export default function ScenarioEditor({ open, onClose, row, env, envState, onSave }) {
  const [draft, setDraft] = useState(row || {});

  useEffect(() => {
    if (!row) return;
    /*
      Populate the direct-edit fields the mock doesn't carry yet, so
      the drawer opens with defaults derived from the row rather than
      empty controls. If the mock ever starts storing them explicitly,
      those win.
    */
    setDraft({
      ...row,
      caller: row.caller || deriveCaller(row.persona),
      backgroundNoise: row.backgroundNoise || deriveNoise(row.persona),
    });
  }, [row]);

  if (!row) return null;

  const set = (k) => (v) => setDraft((d) => ({ ...d, [k]: v }));
  const setCaller = (k) => (v) => setDraft((d) => ({ ...d, caller: { ...(d.caller || {}), [k]: v } }));

  const isConversational = CONVERSATIONAL_SURFACES.includes(env?.surface);
  const isVoice = VOICE_ONLY_SURFACES.includes(env?.surface);
  const dirty = JSON.stringify(draft) !== JSON.stringify(row);

  return (
    <SideDrawer open={open} onClose={onClose} width={620}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Edit scenario</Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              Changes apply to this environment&apos;s copy, not the pack it came from
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Stack spacing={2.5} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
          {/* ─── Directly editable ─── */}
          <SectionHeader
            title="Directly editable"
            hint="These parts don't touch the verification proof, so we let you save them straight to the row."
          />

          <TextField
            size="small" label="Name" value={draft.name || ""}
            onChange={(e) => set("name")(e.target.value)}
            helperText="Kebab-case identifier for this scenario — e.g. polite-senior-verify-identity."
            InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
          />
          <TextField
            size="small" label="Use case" value={draft.useCase || ""}
            onChange={(e) => set("useCase")(e.target.value)}
            helperText="The user-facing sentence describing what this group of scenarios tests."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <TextField
            size="small" label="Branch" value={draft.branchCategory || ""}
            onChange={(e) => set("branchCategory")(e.target.value)}
            helperText="What makes this one different from its siblings — e.g. Verify Identity Path Rushed."
            InputProps={{ sx: { typography: "s2" } }}
          />
          <TextField
            size="small" label="Passes when" multiline minRows={2} value={draft.expected || ""}
            onChange={(e) => set("expected")(e.target.value)}
            helperText="What a pass looks like. Evals grade against this."
            InputProps={{ sx: { typography: "s2" } }}
          />

          {/* ─── Caller (voice / chat only) ─── */}
          {isConversational && (
            <>
              <SectionHeader
                title="Caller"
                hint="How the caller comes across in the run. Voice/chat surfaces only — a browser or coding agent has no caller."
              />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Tone</InputLabel>
                  <Select
                    label="Tone" value={draft.caller?.tone || "neutral"}
                    onChange={(e) => setCaller("tone")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {TONE_OPTIONS.map((o) => <MenuItem key={o} value={o} sx={{ typography: "s2" }}>{o}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Style</InputLabel>
                  <Select
                    label="Style" value={draft.caller?.style || "casual"}
                    onChange={(e) => setCaller("style")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {STYLE_OPTIONS.map((o) => <MenuItem key={o} value={o} sx={{ typography: "s2" }}>{o}</MenuItem>)}
                  </Select>
                </FormControl>
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Accent</InputLabel>
                  <Select
                    label="Accent" value={draft.caller?.accent || "US"}
                    onChange={(e) => setCaller("accent")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {ACCENT_OPTIONS.map((o) => <MenuItem key={o} value={o} sx={{ typography: "s2" }}>{o}</MenuItem>)}
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel>Language</InputLabel>
                  <Select
                    label="Language" value={draft.caller?.language || "English"}
                    onChange={(e) => setCaller("language")(e.target.value)}
                    sx={{ typography: "s2" }}
                  >
                    {LANGUAGE_OPTIONS.map((o) => <MenuItem key={o} value={o} sx={{ typography: "s2" }}>{o}</MenuItem>)}
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
              <Box>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
                    Max turns
                  </Typography>
                  <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}>
                    ~{draft.turns || 0}
                  </Typography>
                </Stack>
                <Slider
                  size="small" min={2} max={20} value={draft.turns || 0}
                  onChange={(_, v) => set("turns")(v)}
                />
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  How long the run is allowed to go before it&apos;s called off.
                </Typography>
              </Box>

              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                  Background noise
                </Typography>
                <ToggleButtonGroup
                  size="small" exclusive
                  value={draft.backgroundNoise || "none"}
                  onChange={(_, v) => v && set("backgroundNoise")(v)}
                  sx={{
                    "& .MuiToggleButton-root": {
                      typography: "s2", fontWeight: 600, textTransform: "none", px: 1.5, py: 0.375,
                      color: "text.secondary", borderColor: "divider",
                      "&.Mui-selected": {
                        bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.09),
                        color: "#7857FC", borderColor: "#7857FC",
                      },
                    },
                  }}
                >
                  {NOISE_OPTIONS.map((n) => (
                    <ToggleButton key={n} value={n}>{n}</ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Box>
            </>
          )}

          {/* ─── Twin seed override (twin-backed envs only) ─── */}
          {envState?.twinBacking?.services?.length > 0 && (
            <>
              <SectionHeader
                title="Clone seed override"
                hint="Optional. Override the env-level twin state for this scenario only — e.g. Slack starts with a specific DM thread, Notion starts with a specific page. Leave blank to inherit the env default."
              />
              <TextField
                size="small" multiline minRows={4}
                label="Seed prompt (natural language)"
                value={draft.twinSeedPrompt || ""}
                onChange={(e) => set("twinSeedPrompt")(e.target.value)}
                placeholder="e.g. Slack #support has 3 messages from the same angry customer; the last one asks to escalate."
                helperText="We resolve this into concrete twin state at run time. Each run starts from a fresh copy."
                InputProps={{ sx: { typography: "s2" } }}
              />
            </>
          )}

          {/* ─── Edit via the builder ─── */}
          <SectionHeader
            title="Edit through the builder"
            hint="These parts are locked to the verification proof. Ask the builder on the left to change them — it'll re-run the checks and tell you if the change broke the scenario."
          />

          <LockedRow
            label="What the caller wants"
            value={draft.task}
            prompt={`change what the caller wants in ${draft.name} to …`}
          />
          <LockedRow
            label="Setup / world state"
            value={draft.setup || "seeded from the environment"}
            prompt={`change the setup for ${draft.name} to …`}
          />
          <LockedRow
            label="What we measure (checks)"
            value={
              draft.checks?.length
                ? draft.checks.map((c) => c.label || c.id).join(" · ")
                : "graded against the passes-when line + the derived sub-goals"
            }
            prompt={`change the checks on ${draft.name} to …`}
          />
          <LockedRow
            label="Sub-tasks (what the runner watches)"
            value={
              draft.subTasks?.length
                ? `${draft.subTasks.length} steps: ${draft.subTasks.map((s) => s.label).join(" → ")}`
                : "derived from the tools + rules this scenario touches"
            }
            prompt={`rewrite the sub-tasks on ${draft.name}`}
          />

          <Stack
            direction="row" spacing={1.25} alignItems="flex-start"
            sx={{
              p: 1.75, borderRadius: 1.25, border: "1px solid",
              borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.28 : 0.2),
              bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.09 : 0.04),
            }}
          >
            <Iconify
              icon="solar:shield-check-linear"
              width={15}
              sx={{
                color: (t) => (t.palette.mode === "dark" ? "#A792FD" : "#7857FC"),
                flexShrink: 0, mt: "1px",
              }}
            />
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Every scenario got pre-verified before it landed: the world was set up, a known-correct
              run had to pass, and the checks had to fail when nothing was done. Free-text edits to
              the task, setup or checks would silently break that proof — that&apos;s why they move
              through the builder, which re-runs the check on save.
            </Typography>
          </Stack>
        </Stack>

        <Stack
          direction="row" justifyContent="flex-end" spacing={1}
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
            Cancel
          </Button>
          <Button
            variant="contained" color="primary" size="small" disabled={!dirty}
            onClick={() => { onSave(draft); onClose(); }}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Save scenario
          </Button>
        </Stack>
      </Stack>
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
};

function SectionHeader({ title, hint }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3", fontWeight: 700, color: "text.primary",
          textTransform: "uppercase", letterSpacing: 0.6, mb: 0.375,
        }}
      >
        {title}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{hint}</Typography>
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
function LockedRow({ label, value, prompt }) {
  return (
    <Box
      sx={{
        p: 1.5, borderRadius: 1.25,
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={13} sx={{ color: "text.subtitle", mt: "3px", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", mb: 0.375, textTransform: "uppercase", letterSpacing: 0.4 }}>
            {label}
          </Typography>
          <Typography
            sx={{
              typography: "s2", color: "text.primary",
              display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden",
            }}
          >
            {value || "—"}
          </Typography>
          {/*
            Mode-aware purple — the brand's #7857FC clears WCAG on white
            but sits close to the dark background in dark theme, so the
            example prompt reads as faint. Switching to the lighter
            purple.light (#A792FD) in dark restores contrast without
            changing the light-theme look.
          */}
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 1 }}>
            <Iconify
              icon="solar:chat-round-line-linear"
              width={13}
              sx={{ color: (t) => (t.palette.mode === "dark" ? "#A792FD" : "#7857FC") }}
            />
            <Typography
              sx={{
                typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                color: (t) => (t.palette.mode === "dark" ? "#A792FD" : "#7857FC"),
              }}
            >
              &ldquo;{prompt}&rdquo;
            </Typography>
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}
LockedRow.propTypes = { label: PropTypes.string, value: PropTypes.string, prompt: PropTypes.string };
