import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, TextField, MenuItem, Fade,
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import { nextAgentVersion } from "../_mock/versions";
import Iconify from "src/components/iconify";
import { BootSequence } from "../components/loading";
import { SYNC_SOURCES, SYNC_STEPS, versionDiff, suiteChoices } from "../_mock/agentSync";

/**
 * Add a new agent version.
 *
 * Three moves: point at the newer agent, look at what changed, then choose
 * what the next score is allowed to mean. That last choice is the whole
 * screen — re-deriving the suite and then comparing the number to the previous
 * version is the standard way to manufacture an improvement, so the two
 * options state their consequence instead of sitting there as equal buttons.
 */
export default function NewAgentVersion({ env, envState, scenarioCount, open, onClose, onCreate }) {
  const [phase, setPhase] = useState("source"); // source → syncing → diff → done
  const [kind, setKind] = useState("git");
  const [value, setValue] = useState("");
  const [choice, setChoice] = useState("freeze");

  const diff = versionDiff(env);
  /* The label this will mint. Versions come from what the environment already
     has, not from how many times anyone has run it. */
  const minted = nextAgentVersion(envState);
  const choices = suiteChoices(env, diff, scenarioCount);
  const source = SYNC_SOURCES.find((s) => s.id === kind);

  const close = () => { onClose(); setTimeout(() => setPhase("source"), 250); };

  return (
    <SideDrawer open={open} onClose={close} width={560}>
      <Stack sx={{ height: "100%" }}>
        <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box flex={1}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>Add an agent version</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              The environment stays put — only the agent changes
            </Typography>
          </Box>
          <IconButton size="small" onClick={close}>
            <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, overflowY: "auto", p: 2.5 }}>
          {/* ── 1. where from ── */}
          {phase === "source" && (
            <Stack spacing={2}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Point us at the newer agent. We read its contract the same way we read the first
                one, then seal it as an immutable version so a result can always be traced back
                to exactly what ran.
              </Typography>

              <Stack spacing={1}>
                {SYNC_SOURCES.map((s) => (
                  <Stack
                    key={s.id}
                    direction="row" alignItems="center" spacing={1.75}
                    onClick={() => setKind(s.id)}
                    sx={{
                      p: 1.75, borderRadius: 1.25, cursor: "pointer", border: "1px solid",
                      borderColor: kind === s.id ? "primary.main" : "divider",
                      bgcolor: (t) => kind === s.id ? alpha(t.palette.primary.main, 0.06) : "transparent",
                    }}
                  >
                    <Iconify icon={s.icon} width={18} sx={{ color: kind === s.id ? "primary.main" : "text.subtitle", flexShrink: 0 }} />
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{s.label}</Typography>
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{s.blurb}</Typography>
                    </Box>
                  </Stack>
                ))}
              </Stack>

              <TextField
                size="small" fullWidth
                label={kind === "git" ? "Branch, tag or commit" : kind === "endpoint" ? "Endpoint" : "Bundle"}
                placeholder={source?.placeholder}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                helperText={kind === "git" ? "Pinned — we record the exact commit we read, not just the branch name." : " "}
                InputProps={{ sx: { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
              />
            </Stack>
          )}

          {phase === "syncing" && (
            <BootSequence steps={SYNC_STEPS} accent="#7857FC" stepMs={800} onDone={() => setPhase("diff")} />
          )}

          {/* ── 2. what changed, 3. what it should mean ── */}
          {phase === "diff" && (
            <Fade in timeout={300}>
              <Stack spacing={2.5}>
                <Box
                  sx={{
                    p: 1.75, borderRadius: 1.25, border: "1px solid", borderColor: "divider",
                    bgcolor: "background.neutral",
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Iconify icon="solar:lock-keyhole-minimalistic-bold" width={15} sx={{ color: "#16A34A" }} />
                    <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      agent {minted.label}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      sealed from {diff.ref} · immutable
                    </Typography>
                  </Stack>
                </Box>

                <Box>
                  <Label>What changed in the contract</Label>
                  <Stack spacing={0.875}>
                    {[
                      ...diff.added.map((d) => ({ ...d, sign: "+", color: "#16A34A" })),
                      ...diff.changed.map((d) => ({ ...d, sign: "~", color: "#CA8A04" })),
                      ...diff.removed.map((d) => ({ ...d, sign: "−", color: "#DC2626" })),
                    ].map((d) => (
                      <Stack key={d.name} direction="row" spacing={1.25} alignItems="flex-start">
                        <Typography sx={{ typography: "s1", fontWeight: 700, color: d.color, width: 12, flexShrink: 0 }}>
                          {d.sign}
                        </Typography>
                        <Box minWidth={0}>
                          <Typography sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                            {d.name}
                          </Typography>
                          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{d.note}</Typography>
                        </Box>
                      </Stack>
                    ))}
                  </Stack>
                </Box>

                <Box>
                  <Label>What the next score should mean</Label>
                  <Stack spacing={1.25}>
                    {choices.map((c) => (
                      <Box
                        key={c.id}
                        onClick={() => setChoice(c.id)}
                        sx={{
                          p: 1.75, borderRadius: 1.25, cursor: "pointer", border: "1px solid",
                          borderColor: choice === c.id ? c.tone : "divider",
                          bgcolor: (t) => choice === c.id ? alpha(c.tone, t.palette.mode === "dark" ? 0.1 : 0.05) : "transparent",
                        }}
                      >
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Iconify
                            icon={choice === c.id ? "solar:record-circle-bold" : "solar:circle-linear"}
                            width={15}
                            sx={{ color: choice === c.id ? c.tone : "text.subtitle", flexShrink: 0 }}
                          />
                          <Typography sx={{ typography: "s2", fontWeight: 700 }}>{c.label}</Typography>
                          {c.recommended && (
                            <Typography sx={{ typography: "s3", fontWeight: 700, color: c.tone }}>· recommended</Typography>
                          )}
                        </Stack>
                        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5, ml: 3 }}>
                          {c.blurb}
                        </Typography>
                        <Stack spacing={0.25} sx={{ mt: 1, ml: 3 }}>
                          {c.consequences.map((x) => (
                            <Stack key={x} direction="row" spacing={0.875} alignItems="flex-start">
                              <Box sx={{ width: 3, height: 3, borderRadius: "50%", bgcolor: "text.subtitle", mt: "7px", flexShrink: 0 }} />
                              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{x}</Typography>
                            </Stack>
                          ))}
                        </Stack>
                      </Box>
                    ))}
                  </Stack>
                </Box>
              </Stack>
            </Fade>
          )}
        </Box>

        <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
          <Box flex={1} />
          <Button onClick={close} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
          <Button
            variant="contained" color="primary"
            disabled={phase === "syncing"}
            onClick={() => {
              if (phase === "source") { setPhase("syncing"); return; }
              /* Minting is the event that moves the pairing forward — the next
                 run pins this version rather than inventing one of its own. */
              onCreate?.(diff.changed?.[0]?.note || "Contract re-read from a newer build.");
              close();
            }}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            {phase === "source" ? "Read this version" : phase === "diff" ? `Create agent ${minted.label}` : "Working…"}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

NewAgentVersion.propTypes = {
  env: PropTypes.object, scenarioCount: PropTypes.number,
  open: PropTypes.bool, onClose: PropTypes.func, onCreate: PropTypes.func, envState: PropTypes.object,
};

function Label({ children }) {
  return (
    <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 1 }}>
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
