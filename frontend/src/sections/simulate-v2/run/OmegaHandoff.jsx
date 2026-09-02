import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Tooltip, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";

/**
 * Handing a change over to Git rather than minting a version here.
 *
 * The primary path (Create agent version, next to this) forks the agent
 * code we already hold, applies the accepted diffs and mints v_next
 * ready to run. This is the alternative: teams that would rather see
 * the diff in a review tool — a PR, a patch, a ticket — before it
 * becomes a version. Merge and deploy on their side; the re-run holds
 * the projection against what actually shipped.
 *
 * The old "we don't hold the code" framing is gone; the concept shifted
 * when the environment started being built from the agent's source. But
 * the *option* to hand off remains, because plenty of teams have a
 * review pipeline they'd rather not skip.
 */

const WAYS = [
  {
    id: "pr",
    label: "Raise a pull request",
    icon: "solar:code-square-linear",
    needs: "repo",
    blurb: "Opens a PR against the branch this environment was built from, with the diff and a link back to this run.",
  },
  {
    id: "patch",
    label: "Copy the patch",
    icon: "solar:copy-linear",
    blurb: "The changes as text, to paste wherever your prompt actually lives.",
  },
  {
    id: "ticket",
    label: "Send to the team",
    icon: "solar:inbox-line-linear",
    blurb: "Files the diff and the evidence as a ticket for whoever owns the agent.",
  },
];

export default function OmegaHandoff({ env, envState, patch, included, projected, current, willFix, onRerun }) {
  const [way, setWay] = useState(null);
  const [sent, setSent] = useState(null);
  const [draft, setDraft] = useState("");

  /*
    A PR needs somewhere to raise it, and a connected endpoint does not come
    with one — we call the agent over HTTP, which tells us nothing about where
    its code lives. So the repository is its own connection rather than
    something inferred, and until it exists this option asks for it instead of
    sitting there greyed out forever.
  */
  const repo = envState?.agentRepo?.url || (env?.builtFrom?.kind === "repo" ? env.builtFrom.value : null);
  const branch = `omega/${env?.id || "agent"}-${included.length}-changes`;

  /* `patchText`, not `patch` — the prop of that name is the state patcher, and
     shadowing it here meant "Connect" tried to call a string. */
  const patchText = included
    .map((p) => [`# ${p.kind}: ${p.title}`, ...p.diff.map((d) => `${d.type === "add" ? "+" : "-"} ${d.text}`)].join("\n"))
    .join("\n\n");

  const send = (id) => {
    if (id === "patch") navigator.clipboard?.writeText(patchText).catch(() => {});
    setSent(id);
  };

  if (!included.length) {
    return (
      <SectionCard>
        <Stack
          direction="row" alignItems="center" spacing={3}
          sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}
        >
          <Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Current pass rate</Typography>
            <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
              {current}%
            </Typography>
          </Box>
          <Box flex={1} />
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            Include a change below to see what it would do.
          </Typography>
        </Stack>
      </SectionCard>
    );
  }

  return (
    <SectionCard>
      <Stack
        direction="row" alignItems="center" spacing={3} flexWrap="wrap" rowGap={1.5}
        sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}
      >
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Current pass rate</Typography>
          <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
            {current}%
          </Typography>
        </Box>
        <Iconify icon="solar:arrow-right-linear" width={20} sx={{ color: "text.subtitle" }} />
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Projected with {included.length} {included.length === 1 ? "change" : "changes"}
          </Typography>
          <Typography sx={{ typography: "m2", fontWeight: 700, color: "#16A34A", fontVariantNumeric: "tabular-nums" }}>
            {projected}%
          </Typography>
          {willFix > 0 && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              if all {willFix} addressed {willFix === 1 ? "task" : "tasks"} pass
            </Typography>
          )}
        </Box>
      </Stack>

      {/*
        The sentence that keeps the two paths straight. The primary flow
        (Create agent version, in the optimizer above) forks the agent's
        code here and mints v_next; this route is for teams that would
        rather see the diff in a review tool before it becomes a version.
      */}
      <Stack
        direction="row" alignItems="flex-start" spacing={1.25}
        sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Prefer to review the diff before it becomes a version? Take the change out as a PR, a patch, or a ticket
          instead. Merge and deploy on your side, then re-run — the projection gets held against what actually
          happened.
        </Typography>
      </Stack>

      <Stack sx={{ px: 2.5, py: 2 }} spacing={1.25}>
        <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>
          {WAYS.map((w) => {
            const needsRepo = w.needs === "repo" && !repo;
            const active = way === w.id;
            return (
              <Tooltip key={w.id} arrow title={w.blurb}>
                <Box component="span">
                  <Button
                    size="small"
                    variant={active ? "contained" : "outlined"}
                    onClick={() => setWay(w.id)}
                    startIcon={<Iconify icon={needsRepo ? "solar:link-linear" : w.icon} width={15} />}
                    sx={{
                      typography: "s2", fontWeight: 700,
                      ...(!active && { color: "text.primary", borderColor: "divider" }),
                    }}
                  >
                    {needsRepo ? "Connect a repository" : w.label}
                  </Button>
                </Box>
              </Tooltip>
            );
          })}
        </Stack>

        {way && (
          <Box
            sx={{
              px: 2, py: 1.75, borderRadius: 1, border: "1px solid", borderColor: "divider",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.015),
            }}
          >
            {way === "pr" && !repo && (
              <Stack spacing={1.5}>
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  This environment reaches your agent at an endpoint, which tells us nothing about where its
                  code lives. Point us at the repository and Omega can raise the PR there — read access to open
                  it, nothing else.
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" rowGap={1}>
                  <TextField
                    size="small"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="https://github.com/your-org/your-agent"
                    sx={{ flex: 1, minWidth: 260, "& .MuiInputBase-input": { typography: "s2" } }}
                  />
                  <Button
                    size="small" variant="contained" color="primary"
                    disabled={!draft.trim()}
                    onClick={() => patch?.({ agentRepo: { url: draft.trim(), connectedAt: new Date().toISOString() } })}
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    Connect
                  </Button>
                </Stack>
              </Stack>
            )}

            {way === "pr" && repo && (
              <Stack spacing={1.25}>
                <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" rowGap={0.5}>
                  <Iconify icon="solar:code-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                  <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" }}>
                    {repo}
                  </Typography>
                  <Chip
                    size="small" label={branch}
                    sx={{
                      height: 19, borderRadius: 0.5, color: "text.secondary",
                      border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                      "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" },
                    }}
                  />
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {included.length} {included.length === 1 ? "change" : "changes"} across{" "}
                  {[...new Set(included.map((p) => p.kind))].join(", ").toLowerCase()}. The PR body carries the
                  scenarios each one addresses and links back to this run, so a reviewer who was not here can
                  tell what evidence it came from.
                </Typography>
              </Stack>
            )}

            {way === "patch" && (
              <Box
                component="pre"
                sx={{
                  m: 0, maxHeight: 220, overflow: "auto", typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", whiteSpace: "pre-wrap",
                }}
              >
                {patchText}
              </Box>
            )}

            {way === "ticket" && (
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                Files one ticket per change, each carrying its diff, the scenarios it addresses and the trace
                links — so the person who picks it up can see the failure rather than take your word for it.
              </Typography>
            )}

            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ mt: 2, display: way === "pr" && !repo ? "none" : "flex" }}
            >
              <Button
                size="small" variant="contained" color="primary"
                disabled={sent === way}
                onClick={() => send(way)}
                startIcon={<Iconify icon={sent === way ? "solar:check-circle-bold" : "solar:arrow-right-up-linear"} width={15} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                {sent === way
                  ? (way === "pr" ? "Pull request opened" : way === "patch" ? "Copied" : "Ticket filed")
                  : (way === "pr" ? "Open the pull request" : way === "patch" ? "Copy to clipboard" : "File the ticket")}
              </Button>
              {sent === way && way === "pr" && (
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  Waiting on review. Nothing has changed in your agent until it is merged and deployed.
                </Typography>
              )}
            </Stack>
          </Box>
        )}
      </Stack>

      {/*
        The re-run is where a version gets minted, because it is the first point
        at which the agent might actually be different.
      */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1 }}>
          Once it is deployed, re-run the same scenarios at the same seed and the projection gets held against
          what actually happened.
        </Typography>
        <Button
          size="small" variant={sent ? "contained" : "outlined"} color="primary"
          onClick={onRerun}
          startIcon={<Iconify icon="solar:play-bold" width={15} />}
          sx={{
            flexShrink: 0, typography: "s2", fontWeight: 700,
            ...(!sent && { color: "text.primary", borderColor: "divider" }),
          }}
        >
          It is deployed — re-run
        </Button>
      </Stack>
    </SectionCard>
  );
}

OmegaHandoff.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  included: PropTypes.array,
  projected: PropTypes.number,
  current: PropTypes.number,
  willFix: PropTypes.number,
  onRerun: PropTypes.func,
};
