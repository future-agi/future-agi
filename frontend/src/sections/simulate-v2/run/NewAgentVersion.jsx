import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { changeFileFor } from "../_mock/optimize";
import { nextAgentVersion, currentAgentVersion } from "../_mock/versions";

/**
 * Create a new agent version from a diagnosis.
 *
 * Product concept shift: the environment was built from the agent's own
 * source (repo / upload), so we hold the code, not just a phone number.
 * When a diagnosis produces changes worth making, the honest next step is
 * to fork the current agent — apply the accepted changes as a bundled
 * diff — and mint that as the next agent version, right here. The user
 * then runs the new version against the same environment and the existing
 * compare feature reads v1 against v2 on the same scenarios.
 *
 * A version therefore carries:
 *   applied         — the changes bundled into it, each with its file and
 *                     diff. Preserved on the version so six weeks later
 *                     "what was different about v3" is a fact, not an
 *                     archaeology exercise.
 *   basedOnVersion  — the version these changes were applied on top of.
 *   fromRunId       — the run whose diagnosis produced them.
 *
 * `nextAgentVersion` in versions.js writes all three into the record.
 *
 * Env-layer changes (Verifier / Reward spec) are excluded here — they
 * belong to the environment, not the agent, and the diagnosis's own
 * "Apply to checks" mints an env version separately.
 */
export default function NewAgentVersion({
  env, envState, included, projected, current, willFix, onCreate, onRun,
}) {
  const nextVer = nextAgentVersion(envState);
  const baseVer = currentAgentVersion(envState);
  const [note, setNote] = useState(defaultNote(included));
  const [created, setCreated] = useState(false);

  /* Group the accepted changes by which file they touch, so the preview
     reads like a real PR diff — one file heading, its changes below,
     rather than a flat list of "kind: title" that reader can't map back
     onto a codebase. Env-layer proposals are filtered out here. */
  const filedChanges = included
    .map((p) => ({ p, file: changeFileFor(env, p) }))
    .filter((row) => row.file);
  const byFile = filedChanges.reduce((acc, row) => {
    const key = row.file.path;
    acc[key] = acc[key] || { file: row.file, changes: [] };
    acc[key].changes.push(row.p);
    return acc;
  }, {});
  const fileList = Object.values(byFile);

  const nonCodeChanges = included.length - filedChanges.length;

  const handleCreate = () => {
    onCreate?.({ note, applied: filedChanges.map((r) => r.p) });
    setCreated(true);
  };

  /* ── success state ── */
  if (created) {
    return (
      <SectionCard>
        <Stack
          direction="row" alignItems="center" spacing={2}
          sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.1 : 0.06) }}
        >
          <Box
            sx={{
              width: 34, height: 34, borderRadius: 999, display: "grid", placeItems: "center",
              bgcolor: (t) => alpha("#16A34A", 0.18), flexShrink: 0,
            }}
          >
            <Iconify icon="solar:check-circle-bold" width={18} sx={{ color: "#16A34A" }} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>
              Agent {nextVer.label} created
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              {filedChanges.length} {filedChanges.length === 1 ? "change" : "changes"} applied on top of {baseVer.label}.
            </Typography>
          </Box>
        </Stack>

        {/*
          The primary next question after minting: run it. The v2 result
          lands as a new row in the simulation runs table, tagged with
          agent {nextVer.label}, and the existing compare flow reads it
          against v1's row when the user wants to.
        */}
        <Stack
          direction="row" alignItems="center" spacing={1.5}
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
        >
          <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1 }}>
            Run {nextVer.label} against this environment now?
          </Typography>
          <Button
            variant="contained" color="primary" size="small"
            onClick={() => onRun?.(nextVer)}
            startIcon={<Iconify icon="solar:play-bold" width={14} />}
            sx={{ flexShrink: 0, typography: "s2", fontWeight: 700 }}
          >
            Run in simulation
          </Button>
        </Stack>
      </SectionCard>
    );
  }

  return (
    <SectionCard>
      {/* ── header — the projection reader already had ── */}
      <Stack
        direction="row" alignItems="center" spacing={3} flexWrap="wrap" rowGap={1.5}
        sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}
      >
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Now on {baseVer.label}</Typography>
          <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
            {current}%
          </Typography>
        </Box>
        <Iconify icon="solar:arrow-right-linear" width={20} sx={{ color: "text.subtitle" }} />
        <Box>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {nextVer.label} projected · {included.length} {included.length === 1 ? "change" : "changes"}
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

      {/* ── explanation, once — the concept is new and unusual ── */}
      <Stack
        direction="row" alignItems="flex-start" spacing={1.25}
        sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="solar:info-circle-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          This forks the agent from <b>{baseVer.label}</b>, applies the accepted changes as a bundled diff, and mints
          it as agent <b>{nextVer.label}</b>. Nothing is deployed — running <b>{nextVer.label}</b> against this environment produces
          numbers you can hold against <b>{baseVer.label}</b> in the compare view.
        </Typography>
      </Stack>

      {/* ── diff preview, grouped by file ── */}
      <Stack sx={{ px: 2.5, py: 2 }} spacing={1.5}>
        {fileList.length === 0 ? (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            Nothing you accepted lands on the agent code — the environment-side fixes above mint an environment version instead.
          </Typography>
        ) : (
          fileList.map(({ file, changes }) => (
            <FileDiff key={file.path} file={file} changes={changes} />
          ))
        )}
        {nonCodeChanges > 0 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {nonCodeChanges} env-side {nonCodeChanges === 1 ? "change is" : "changes are"} handled by the environment version above and will not be part of {nextVer.label}.
          </Typography>
        )}
      </Stack>

      {/* ── the note that travels with the version ── */}
      <Stack sx={{ px: 2.5, pb: 1.75 }} spacing={0.75}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Version note
        </Typography>
        <TextField
          size="small" value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={`What is different about ${nextVer.label}`}
          InputProps={{ sx: { typography: "s2" } }}
        />
      </Stack>

      {/* ── actions ── */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1 }}>
          {nextVer.label} is created here, run separately.
        </Typography>
        <Button
          variant="contained" color="primary" size="small"
          disabled={!fileList.length}
          onClick={handleCreate}
          startIcon={<Iconify icon="solar:branching-paths-up-linear" width={15} />}
          sx={{ flexShrink: 0, typography: "s2", fontWeight: 700 }}
        >
          Create agent {nextVer.label}
        </Button>
      </Stack>
    </SectionCard>
  );
}

NewAgentVersion.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  included: PropTypes.array,
  projected: PropTypes.number,
  current: PropTypes.number,
  willFix: PropTypes.number,
  onCreate: PropTypes.func,
  onRun: PropTypes.func,
};

/*
  One file's worth of changes as a code diff. Reads as a real PR file:
  filepath header with a language chip, then each change as a title +
  add/remove lines below. The lines carry the same +/- prefix and colouring
  as the diagnosis panel above so the same change reads the same in both.
*/
function FileDiff({ file, changes }) {
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{
          px: 1.5, py: 0.875,
          bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <Iconify icon="solar:file-text-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography
          sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", flex: 1, minWidth: 0 }}
          noWrap
        >
          {file.path}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.disabled" }}>
          {file.language}
        </Typography>
      </Stack>

      <Stack divider={<Box sx={{ borderTop: "1px dashed", borderColor: "divider" }} />}>
        {changes.map((p) => (
          <Box key={p.id} sx={{ px: 1.5, py: 1.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.5 }}>
              {p.title}
            </Typography>
            <Stack spacing={0.375}>
              {p.diff.map((d, i) => (
                <Stack
                  key={`${d.type}-${i}`} direction="row" spacing={1}
                  sx={{
                    px: 1, py: 0.5, borderRadius: 0.5,
                    bgcolor: (t) => alpha(d.type === "add" ? "#16A34A" : "#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                      fontWeight: 700, flexShrink: 0,
                      color: d.type === "add" ? "#16A34A" : "#DC2626",
                    }}
                  >
                    {d.type === "add" ? "+" : "−"}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                      color: d.type === "add" ? "text.primary" : "text.disabled",
                      textDecoration: d.type === "add" ? "none" : "line-through",
                    }}
                  >
                    {d.text}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

FileDiff.propTypes = { file: PropTypes.object, changes: PropTypes.array };

/*
  A first-pass version note derived from the accepted changes — kinds
  joined, or the single change's title if only one was accepted. Users can
  overwrite; this is just so the field is never blank at commit time.
*/
function defaultNote(included) {
  if (!included?.length) return "";
  if (included.length === 1) return included[0].title;
  const kinds = [...new Set(included.map((p) => p.kind.toLowerCase()))];
  return `${included.length} changes: ${kinds.join(", ")}`;
}
