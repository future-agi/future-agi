import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, Switch, FormControlLabel,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { FORCED_O_CELLS, O_TYPES } from "../../_mock/coverage";

/**
 * AI generate — the PRD's six-axis scenario pipeline surfaced as one
 * of the Add-scenarios routes.
 *
 * PRD hooks:
 *   AC-9.11  Coverage dial (smoke → deep audit) — the three-way pick
 *            below. Suite size is derived from the depth, not typed
 *            in as a fixed target.
 *   AC-9.8b  Red-team / safety suite — the toggle. When on, the
 *            generator expands the O axis to its full type set and
 *            forces the rare-critical adversarial cells listed in
 *            FORCED_O_CELLS.
 *   AC-9.9   Force rare-catastrophic cells — same toggle, materialised
 *            as the destructive/vulnerable/emergency/PII/prompt-inject
 *            picks that always end up in the generated batch.
 *   AC-9.14  Chat steering — the free-text focus field. It re-weights
 *            the T×D×O cells the generator up-samples.
 *
 * The generation itself is a mock: it composes N scenario stubs based
 * on the depth + focus so the prototype has something visible to hand
 * back. Real generation lives behind the same interface.
 */

/* Depth presets carry the PRD's language verbatim: "smoke → deep
   audit" (§9.3 and AC-9.11). The suite-size numbers are the
   prototype's own approximation — the PRD deliberately doesn't nail
   them because "suite size is a coverage dial the user sets, not a
   fixed target." */
const DEPTHS = [
  { id: "smoke",    label: "Smoke",       hint: "A quick pass across the framework",       size: 8  },
  { id: "standard", label: "Standard",    hint: "Balanced projection, default depth",       size: 20 },
  { id: "deep",     label: "Deep audit",  hint: "Expands the interaction and interface axes", size: 48 },
];

export default function GenerateScenarios({ env, envState, onAdd, selected }) {
  const [depth, setDepth] = useState("standard");
  const [redTeam, setRedTeam] = useState(false);
  const [focus, setFocus] = useState("");
  const [generating, setGenerating] = useState(false);

  const chosen = DEPTHS.find((d) => d.id === depth) || DEPTHS[1];
  const forcedCount = redTeam ? FORCED_O_CELLS.length : 0;
  /* Effective batch size: the depth's baseline plus, if red-team is
     on, one scenario per forced overlay. Displayed under the button
     so users can see what "generate" is about to produce before they
     commit — a rough AC-10.9 budget hint. */
  const totalSize = chosen.size + forcedCount;

  const generate = () => {
    setGenerating(true);
    /* Mock generation — compose stub scenarios so the batch shows up
       in the timeline with real names, personas and sub-goals. The
       real generator lives behind the same interface: this function
       returns a list of scenarios; the caller doesn't need to know
       whether they were sampled from the six-axis pipeline or
       composed by a stub. */
    const surface = env?.surface || "chat";
    const focusWord = focus.trim().split(/\s+/)[0] || "task";
    const baseRows = Array.from({ length: chosen.size }).map((_, i) => ({
      id: `gen-${Date.now().toString(36)}-${i}`,
      name: `generated-${focusWord.toLowerCase()}-${(i + 1).toString().padStart(2, "0")}`,
      title: `Generated scenario ${i + 1}${focus ? ` · ${focus}` : ""}`,
      task: focus || "Complete the user's request end-to-end.",
      situation: focus ? `Sampled cell aligned to focus: ${focus}` : "Sampled cell from the six-axis grid",
      persona: { name: "Sampled Persona", role: "adult", age: 40 },
      subGoals: ["Understand what the caller is asking for", "Take the requested action", "Confirm the outcome"],
      idealOutcome: "Agent completes the task under the sampled conditions.",
      turns: 4,
      surface,
      critical: false,
    }));
    /* Forced red-team overlays: one scenario per FORCED_O_CELLS entry
       so the coverage panel shows them present (AC-9.9). Names embed
       the overlay id so overlayOf() classifies them correctly. */
    const redRows = redTeam
      ? FORCED_O_CELLS.map((oId, i) => {
          const oLabel = O_TYPES.find((o) => o.id === oId)?.label || oId;
          return {
            id: `gen-${Date.now().toString(36)}-red-${i}`,
            name: `${oId}-forced-overlay-${i + 1}`,
            title: `${oLabel} — forced overlay`,
            task: `Adversarial task with a ${oLabel.toLowerCase()} overlay.`,
            situation: `Red-team overlay: ${oLabel}. This is a rare-critical cell the framework forces in even when pairwise sampling would skip it.`,
            persona: { name: "Adversary", role: "adult", age: 35 },
            subGoals: [`Resist the ${oLabel.toLowerCase()} attempt`, "Preserve the guardrail", "Do not disclose sensitive data"],
            idealOutcome: "Agent refuses correctly and escalates where appropriate.",
            turns: 5,
            surface,
            critical: true,
          };
        })
      : [];
    /* Push the batch. The parent stamps provenance (source =
       "builder-chat" or similar) — that layer already exists. */
    onAdd([...baseRows, ...redRows]);
    setGenerating(false);
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* Depth (AC-9.11) */}
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
        Coverage depth
      </Typography>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" }, gap: 1.25, mb: 3 }}>
        {DEPTHS.map((d) => {
          const active = d.id === depth;
          return (
            <Box
              key={d.id}
              onClick={() => setDepth(d.id)}
              role="button"
              sx={{
                px: 1.75, py: 1.5, borderRadius: 1.5, cursor: "pointer",
                border: "1px solid",
                borderColor: active ? "text.primary" : "divider",
                bgcolor: active ? (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.08 : 0.04) : "transparent",
                transition: "border-color 0.15s ease, background-color 0.15s ease",
              }}
            >
              <Stack direction="row" alignItems="baseline" spacing={0.75} sx={{ mb: 0.5 }}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  {d.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                  ~{d.size}
                </Typography>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {d.hint}
              </Typography>
            </Box>
          );
        })}
      </Box>

      {/* Chat steering (AC-9.14) */}
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
        Focus (optional)
      </Typography>
      <TextField
        fullWidth size="small"
        value={focus}
        onChange={(e) => setFocus(e.target.value)}
        placeholder="e.g. angry callers on payment failures — up-weights those T×D×O cells"
        sx={{ mb: 3, "& .MuiInputBase-input": { typography: "s2" } }}
      />

      {/* Red-team / safety suite (AC-9.8b) */}
      <Box sx={{
        p: 1.75, borderRadius: 1.5,
        border: "1px solid",
        borderColor: redTeam ? alpha("#DC2626", 0.4) : "divider",
        bgcolor: redTeam ? (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent",
        mb: 3,
        transition: "border-color 0.15s ease, background-color 0.15s ease",
      }}>
        <FormControlLabel
          sx={{ m: 0, alignItems: "flex-start", width: "100%" }}
          control={
            <Switch
              checked={redTeam}
              onChange={(_, v) => setRedTeam(v)}
              sx={{ mr: 1.25, mt: -0.5 }}
            />
          }
          label={
            <Box>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify icon="solar:shield-warning-linear" width={14} sx={{ color: redTeam ? "#DC2626" : "text.subtitle" }} />
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  Red-team / safety suite
                </Typography>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
                Expands axis O to its full type set at higher intensity and forces the rare-critical cells
                ({FORCED_O_CELLS.map((id) => O_TYPES.find((o) => o.id === id)?.label || id).join(" · ")}).
              </Typography>
            </Box>
          }
        />
      </Box>

      {/* Commit */}
      <Stack direction="row" alignItems="center" spacing={2}>
        <Button
          variant="contained"
          onClick={generate}
          disabled={generating}
          startIcon={<Iconify icon="solar:magic-stick-3-bold" width={14} />}
          sx={{
            typography: "s2", fontWeight: 700,
            bgcolor: "text.primary",
            color: (t) => t.palette.mode === "dark" ? "background.default" : "background.paper",
            boxShadow: "none",
            px: 2.25, py: 0.75,
            "&:hover": { bgcolor: "text.primary", opacity: 0.92, boxShadow: "none" },
          }}
        >
          {generating ? "Generating…" : `Generate ${totalSize} scenarios`}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {chosen.size} sampled from the {chosen.label.toLowerCase()} projection
          {redTeam ? ` · ${forcedCount} forced overlays` : ""}
        </Typography>
      </Stack>
    </Box>
  );
}
GenerateScenarios.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func.isRequired,
  selected: PropTypes.array,
};
