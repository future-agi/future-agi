import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, MenuItem, Collapse, Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard, EmptyState } from "../components/primitives";
import { setupGaps, gapCounts, GAP_STATUS, credentialManifest } from "../_mock/setupGaps";

/**
 * Needs your input.
 *
 * Reading an agent gets most of the way and then hits things no amount of
 * reading can settle. The split is what makes this a short list rather than a
 * form: blocking gaps stop a run, assumed gaps do not — the builder guesses,
 * runs anyway, and flags the guess so nothing quietly rests on it.
 *
 * Colour carries the status and nothing else. Every row used to say it three
 * times — a filled tile behind the icon, the status word, and a tinted
 * confidence chip — which on a five-item list is a dozen coloured blocks for
 * two facts. One mark per row now: the icon is bare and coloured, the status
 * word keeps its colour, and everything that merely qualifies an item
 * (confidence, the counts, the all-clear banner) is neutral. Filled tints are
 * what read as "a lot of colour", so there are none left.
 *
 * The status word at the end of each row went too: the icon already carries
 * it, the list is sorted blocking-first, and the counts above name all three
 * states. It only remains as the icon's aria-label, since a colour and a
 * shape are not a label to a screen reader.
 */
export default function SetupGapsPanel({ env, envState, patch, onGo }) {
  const gaps = setupGaps(env, envState);
  const counts = gapCounts(gaps);
  const credentials = credentialManifest(env, envState);

  const answer = (id, value) =>
    patch({ gapsResolved: { ...(envState.gapsResolved || {}), [id]: value } });

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Needs your input</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 760 }}>
          What reading your agent could not settle. Blocking items hold the run; assumed ones
          do not — we guessed, flagged it, and kept going.
        </Typography>
      </Box>

      {/*
        Credentials as a manifest, not a conversation.

        Reading the source says which secrets are needed and where each
        requirement came from, so the list is stated rather than asked — and the
        user is only prompted for the ones nothing already satisfies. Values
        never appear here: a requirement carries a reference, which is what
        lets this be shown, logged and exported without redaction.
      */}
      <Box
        sx={{
          mb: 2, borderRadius: 1.25, border: "1px solid", borderColor: "divider", overflow: "hidden",
        }}
      >
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 1.75, py: 1.25, bgcolor: "background.neutral" }}
        >
          <Iconify icon="solar:key-minimalistic-square-linear" width={15} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Credentials this agent needs</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {credentials.filter((c) => c.status === "configured").length} configured ·{" "}
            {credentials.filter((c) => c.status === "missing").length} missing
          </Typography>
        </Stack>
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {credentials.map((c) => {
            const tone = c.status === "configured" ? "#16A34A" : c.status === "missing" ? "#DC2626" : "#9AA0A6";
            return (
              <Stack key={c.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.75, py: 1 }}>
                {/*
                  Soft tinted medallion instead of a hard solid check/cross.
                  Same treatment as the setup pipeline dots — the earlier
                  version's bold red-and-green punched through the panel and
                  read as errors, not statuses.
                */}
                <StatusDot tone={tone} status={c.status} />
                <Box flex={1} minWidth={0}>
                  <Stack direction="row" alignItems="baseline" spacing={0.875} flexWrap="wrap">
                    <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      {c.id}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{c.provider}</Typography>
                    {!c.required && (
                      <Typography sx={{ typography: "s3", color: "text.disabled" }}>optional</Typography>
                    )}
                  </Stack>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {c.purpose} · detected from {c.detectedFrom}
                  </Typography>
                </Box>
                <Typography noWrap sx={{ typography: "s3", color: tone, fontWeight: 700, flexShrink: 0 }}>
                  {c.satisfiedBy ? `using ${c.satisfiedBy}` : c.status}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </Box>

      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        {["blocking", "assumed", "resolved"].map((k) => (
          <Stack
            key={k}
            direction="row" alignItems="center" spacing={0.75}
            sx={{
              px: 1.25, py: 0.5, borderRadius: 0.875,
              border: "1px solid", borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 700, color: counts[k] ? GAP_STATUS[k].color : "text.subtitle" }}>
              {counts[k]}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>{GAP_STATUS[k].label.toLowerCase()}</Typography>
          </Stack>
        ))}
      </Stack>

      {counts.blocking === 0 && (
        <Box
          sx={{
            p: 1.75, mb: 2, borderRadius: 1.25, border: "1px solid",
            borderColor: "divider", bgcolor: "background.neutral",
          }}
        >
          <Stack direction="row" spacing={1.25} alignItems="flex-start">
            <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "#16A34A", flexShrink: 0, mt: "1px" }} />
            <Typography sx={{ typography: "s2" }}>
              <b>Nothing is blocking a run.</b> The assumed items below still apply — a result that
              depends on one carries its low-confidence flag into the scorecard.
            </Typography>
          </Stack>
        </Box>
      )}

      {gaps.length === 0 ? (
        <EmptyState icon="solar:inbox-linear" title="Nothing to resolve" body="Connect an agent and the builder will tell you what it could not work out on its own." />
      ) : (
        <SectionCard title={`${gaps.length} items`} subtitle="Newest first, blocking at the top">
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {[...gaps].sort((a, b) => (a.status === "blocking" ? -1 : b.status === "blocking" ? 1 : 0)).map((g) => (
              <GapRow key={g.id} gap={g} onAnswer={(v) => answer(g.id, v)} onGo={onGo} />
            ))}
          </Stack>
        </SectionCard>
      )}
    </Box>
  );
}

SetupGapsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  onGo: PropTypes.func,
};

/* ── one gap ─────────────────────────────────────────────────────────────── */

function GapRow({ gap, onAnswer, onGo }) {
  const [open, setOpen] = useState(gap.status === "blocking");
  const [draft, setDraft] = useState(gap.ask?.options?.[0] || "");
  const meta = GAP_STATUS[gap.status];

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1.75}
        onClick={() => setOpen((o) => !o)}
        sx={{ px: 2.5, py: 1.5, cursor: "pointer" }}
      >
        <StatusDot tone={meta.color} status={gap.status} size={26} aria-label={meta.label} />

        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{gap.title}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>· {gap.area}</Typography>
            {gap.confidence && gap.status === "assumed" && (
              <Chip
                size="small"
                label={`${gap.confidence} confidence`}
                sx={{
                  height: 18, borderRadius: 0.75, color: "text.subtitle",
                  border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                  "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
                }}
              />
            )}
          </Stack>
          {!open && gap.assumed && (
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{gap.assumed}</Typography>
          )}
          {!open && gap.answered && (
            <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{gap.answered}</Typography>
          )}
        </Box>

        <Iconify
          icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
          width={15}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.75} sx={{ px: 2.5, pb: 2.25, pl: 6.25 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary", maxWidth: 780 }}>{gap.why}</Typography>

          {gap.ask?.type === "link" ? (
            <Box>
              <Button
                size="small"
                onClick={() => onGo?.(gap.ask.to)}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ typography: "s2", fontWeight: 700, color: "primary.main", px: 0 }}
              >
                {gap.ask.label}
              </Button>
            </Box>
          ) : (
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25} alignItems={{ sm: "flex-end" }}>
              {gap.ask?.type === "choice" ? (
                <TextField
                  select size="small" label={gap.ask.label} value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  sx={{ minWidth: 340 }}
                >
                  {gap.ask.options.map((o) => (
                    <MenuItem key={o} value={o} sx={{ typography: "s2" }}>{o}</MenuItem>
                  ))}
                </TextField>
              ) : (
                <TextField
                  size="small"
                  type="password"
                  label={gap.ask?.label}
                  placeholder={gap.ask?.placeholder}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  sx={{ minWidth: 340 }}
                />
              )}
              <Button
                variant="contained" color="primary" size="small"
                disabled={!draft}
                onClick={() => onAnswer(gap.ask?.type === "secret" ? "Set — stored in the sandbox only" : draft)}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                {gap.status === "resolved" ? "Update" : "Resolve"}
              </Button>
            </Stack>
          )}

          {gap.ask?.type === "secret" && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Held by the sandbox for the length of a run and never written to a scenario, a
              trace or a file you can export.
            </Typography>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
GapRow.propTypes = { gap: PropTypes.object, onAnswer: PropTypes.func, onGo: PropTypes.func };

/*
  Soft tinted circle with a light glyph. Same pattern as the setup pipeline
  medallions — the hard bold red/green icons read as errors rather than as
  statuses, and the room was full of them.
*/
function StatusDot({ tone, status, size = 22, ...aria }) {
  const svg = (() => {
    const stroke = tone;
    const w = Math.round(size * 0.5);
    const common = { width: w, height: w, viewBox: "0 0 24 24", fill: "none", stroke, strokeWidth: 2.5, strokeLinecap: "round", strokeLinejoin: "round" };
    if (status === "configured" || status === "resolved") {
      return <Box component="svg" {...common} sx={{ display: "block" }}><polyline points="5,12.5 10,17.5 19,7" /></Box>;
    }
    if (status === "missing" || status === "blocking") {
      return <Box component="svg" {...common} sx={{ display: "block" }}>
        <line x1="6" y1="6" x2="18" y2="18" />
        <line x1="18" y1="6" x2="6" y2="18" />
      </Box>;
    }
    /* assumed / other → an "info" dot */
    return <Box component="svg" {...common} sx={{ display: "block" }}>
      <circle cx="12" cy="12" r="1.5" fill={stroke} stroke="none" />
      <line x1="12" y1="10" x2="12" y2="17" />
    </Box>;
  })();

  return (
    <Box
      role="img"
      {...aria}
      sx={{
        width: size, height: size, borderRadius: "50%", flexShrink: 0,
        display: "grid", placeItems: "center",
        bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.16 : 0.12),
        border: `1px solid ${alpha(tone, 0.35)}`,
      }}
    >
      {svg}
    </Box>
  );
}
StatusDot.propTypes = { tone: PropTypes.string, status: PropTypes.string, size: PropTypes.number };
