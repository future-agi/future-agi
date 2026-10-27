import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import ChipCard from "../environments/ChipCard";
import { keyRefOf } from "../_mock/secretRefs";
import { DetailRow, FactRow } from "./connect/connectBits";
import SecretRefField from "./connect/SecretRefField";
import { FOUND_FACTS, foundPatchOf, foundValuesOf } from "./foundFacts";

const MONO = "ui-monospace, Menlo, monospace";
const CRED_SLOT = "vault://acme/sim/agent_test_credentials";

/**
 * "Check what we found" — opened from Finish setting up on a code agent's
 * Overview. Each fact we read is confirmed or corrected in place; then what
 * only the person can tell us: where prompts built at run time come from,
 * which tools must never run for real, and the test credentials to use
 * (pasted or by secret-manager reference — never production secrets).
 * Saving writes it all to the agent record; until then a newly connected
 * code agent runs sealed on what we read, but not live.
 */
export default function AgentFoundReview({ agent, onSave, onCancel }) {
  const found = foundValuesOf(agent);
  const v = agent.values || {};
  const [values, setValues] = useState(() =>
    Object.fromEntries(FOUND_FACTS.map((f) => [f.id, found[f.id]])),
  );
  /* id → "confirmed" | "corrected" — every fact needs one before saving. */
  const [status, setStatus] = useState({});
  const [pointers, setPointers] = useState(() =>
    Object.fromEntries(
      found.runtimePrompts.map((p, i) => [i, p.pointer || ""]),
    ),
  );
  /* Safe by default: whatever was marked at connect stays marked. */
  const [neverReal, setNeverReal] = useState(() =>
    (v.neverRunForReal || []).filter((t) => found.tools.includes(t)),
  );
  const [credRef, setCredRef] = useState(v.testCredentialsRef || "");
  /* Test credentials given at connect (.env values, a credential file) are
     the ones — shown here, not asked for again. */
  const givenEnv = v.envValues || [];
  const givenFiles = v.credentialFiles || [];
  const givenAtConnect = givenEnv.length > 0 || givenFiles.length > 0;

  const tools = values.tools || [];
  const open = FOUND_FACTS.filter((f) => !status[f.id]);

  const save = () => {
    const corrected = Object.fromEntries(
      FOUND_FACTS.filter((f) => status[f.id] === "corrected").map((f) => [
        f.id,
        values[f.id],
      ]),
    );
    const runtimePrompts = found.runtimePrompts.map(
      ({ pointer: _old, ...p }, i) => {
        const pointer = (pointers[i] || "").trim();
        return pointer ? { ...p, pointer } : p;
      },
    );
    const n = Object.keys(corrected).length;
    onSave(
      foundPatchOf(agent, {
        corrected,
        runtimePrompts: found.runtimePrompts.length ? runtimePrompts : null,
        neverRunForReal: neverReal.filter((t) => tools.includes(t)),
        /* Stored encrypted: what's kept is the reference it's held under. */
        ...(!givenAtConnect && {
          testCredentialsRef: keyRefOf(credRef, CRED_SLOT),
        }),
      }),
      n
        ? `Saved — ${n} item${n === 1 ? "" : "s"} corrected on ${agent.name}'s record.`
        : `Saved — what we found in ${agent.name} is confirmed.`,
    );
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {FOUND_FACTS.map((f, i) => (
        <FactRow
          key={f.id}
          fact={f}
          first={i === 0}
          value={values[f.id]}
          from={
            f.id === "model" && found.modelAtRuntime
              ? "Chosen at run time from configuration — this is the default it falls back to"
              : f.id === "tests"
                ? `Every change keeps them passing${found.testChecks?.length ? ` · also ${found.testChecks.join(", ")}` : ""}`
                : null
          }
          status={status[f.id]}
          onConfirm={() => setStatus((s) => ({ ...s, [f.id]: "confirmed" }))}
          onCorrect={(next) => {
            setValues((x) => ({ ...x, [f.id]: next }));
            setStatus((s) => ({ ...s, [f.id]: "corrected" }));
          }}
        />
      ))}

      {found.runtimePrompts.length > 0 && (
        <DetailRow
          label="Built at run time"
          from="Point us at where each one comes from, so a simulation sees the same text production does."
        >
          <Stack spacing={1}>
            {found.runtimePrompts.map((p, i) => (
              <Box key={`${p.where}-${p.what}`}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {p.what}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    fontFamily: MONO,
                    mb: 0.5,
                  }}
                >
                  {p.where}
                </Typography>
                <TextField
                  size="small"
                  fullWidth
                  placeholder="A file, URL or Future AGI prompt"
                  value={pointers[i] || ""}
                  onChange={(e) =>
                    setPointers((x) => ({ ...x, [i]: e.target.value }))
                  }
                  inputProps={{ "aria-label": `Where "${p.what}" comes from` }}
                  sx={{
                    "& .MuiInputBase-input": {
                      typography: "s2",
                      fontFamily: MONO,
                    },
                  }}
                />
              </Box>
            ))}
          </Stack>
        </DetailRow>
      )}

      <DetailRow
        label="Never run for real"
        from={
          neverReal.length
            ? `${neverReal.length} of ${tools.length} answer from the environment's world in every simulation.`
            : "None marked — every tool may run as it does in production."
        }
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
          {tools.map((t) => (
            <ChipCard
              key={t}
              label={t}
              on={neverReal.includes(t)}
              onClick={() =>
                setNeverReal((x) =>
                  x.includes(t) ? x.filter((y) => y !== t) : [...x, t],
                )
              }
            />
          ))}
          {!tools.length && (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
              No tools found
            </Typography>
          )}
        </Box>
      </DetailRow>

      <DetailRow
        label="Test credentials"
        from={
          found.credentialKeys.length
            ? `It reads ${found.credentialKeys.join(", ")} — names only, we never read the values.`
            : null
        }
      >
        {givenAtConnect ? (
          <Box>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {[
                ...givenEnv.map((e) => ({
                  k: e.name,
                  val: e.secret ? e.ref : e.value,
                })),
                ...givenFiles.map((f) => ({
                  k: f.name,
                  val: "credential file · mounted per run",
                })),
              ].map((r, i) => (
                <Stack
                  key={r.k}
                  direction="row"
                  spacing={1.5}
                  sx={{
                    px: 1.25,
                    py: 0.75,
                    borderTop: i ? "1px solid" : "none",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      fontFamily: MONO,
                      width: 190,
                      flexShrink: 0,
                    }}
                  >
                    {r.k}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontFamily: MONO,
                      color: "text.secondary",
                      minWidth: 0,
                    }}
                  >
                    {r.val}
                  </Typography>
                </Stack>
              ))}
            </Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
            >
              Given when you connected — test credentials only, never sent to
              the improver.
            </Typography>
          </Box>
        ) : (
          <SecretRefField
            value={credRef}
            onChange={setCredRef}
            placeholder={CRED_SLOT}
            helper="Test credentials only, never production secrets. Stored encrypted and used only by Future AGI — never sent to the improver."
          />
        )}
      </DetailRow>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.75,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {open.length
            ? `Confirm or correct ${open.map((f) => f.label.toLowerCase()).join(", ")} to save.`
            : "Everything is confirmed or corrected."}
        </Typography>
        <Button
          size="small"
          onClick={onCancel}
          sx={{ color: "text.secondary" }}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={open.length > 0}
          onClick={save}
        >
          Save
        </Button>
      </Stack>
    </Box>
  );
}

AgentFoundReview.propTypes = {
  agent: PropTypes.object.isRequired,
  /* (valuesPatch, message) — the agent page's own save. */
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};
