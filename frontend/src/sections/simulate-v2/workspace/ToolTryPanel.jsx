import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CopyCommandButton from "../components/CopyCommandButton";
import { sideEffectOf, tierOf, toolSettingOf } from "../_mock/toolEnv";

const MONO = "ui-monospace, Menlo, monospace";

/* A plausible request for the tool, from its argument names. */
const sampleInput = (tool) => {
  const args = (tool.args || [])
    .map((a) => (typeof a === "string" ? a : a.name))
    .filter(Boolean);
  const value = (a) =>
    /order/.test(a)
      ? "O-104"
      : /customer|user/.test(a)
        ? "C-881"
        : /amount|cents/.test(a)
          ? 4599
          : /email/.test(a)
            ? "customer1@sandbox.test"
            : /date|at$/.test(a)
              ? "2026-10-30"
              : "example";
  return JSON.stringify(
    Object.fromEntries(
      (args.length ? args : ["order_id"]).map((a) => [a, value(a)]),
    ),
    null,
    2,
  );
};

/**
 * Try one request against the tool in a preview world (roadmap actors.md
 * "Tool worlds"): what it answers under its current behaviour, and — for a
 * write — the change it makes, which a later read in the same preview sees.
 * Reset puts the preview back on the seed; completed runs are untouched.
 */
export default function ToolTryPanel({ tool, env, envState }) {
  const setting = toolSettingOf(tool, envState);
  const effect = sideEffectOf(tool);
  const [input, setInput] = useState(() => sampleInput(tool));
  const [log, setLog] = useState([]);
  const [writes, setWrites] = useState(0);

  const send = () => {
    let body;
    try {
      body = JSON.parse(input);
    } catch {
      setLog((l) => [
        ...l,
        { error: "That isn't valid JSON — nothing was sent." },
      ]);
      return;
    }
    const key = Object.values(body)[0];
    const tierWord = tierOf(setting.tier).word;
    const res =
      setting.tier === "stub"
        ? { response: { ok: true, note: "fixed response" }, change: null }
        : setting.tier === "replay"
          ? {
              response: { ok: true, recorded_at: "2026-10-12", id: key },
              change: null,
            }
          : effect === "write"
            ? {
                response: {
                  ok: true,
                  id: key,
                  status: "committed",
                  write: writes + 1,
                },
                change: `${key}: written (${writes + 1} write${writes ? "s" : ""} in this preview)`,
              }
            : {
                response: {
                  ok: true,
                  id: key,
                  status: writes ? "refunded" : "delivered",
                },
                change: null,
                seesWrite: writes > 0,
              };
    if (res.change) setWrites((n) => n + 1);
    setLog((l) => [...l, { tier: tierWord, request: body, ...res }]);
  };

  return (
    <Box
      sx={{
        px: 2.5,
        pl: 7.25,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          Try a request in a preview world
        </Typography>
        <CopyCommandButton
          command={`agent-learn tool preview ${tool.name} --environment ${env?.id || "<env>"} --input ./request.json`}
        />
      </Stack>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <TextField
          size="small"
          multiline
          minRows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          sx={{ flex: 1, "& textarea": { fontFamily: MONO, fontSize: 12 } }}
        />
        <Stack spacing={0.75}>
          <Button
            size="small"
            variant="outlined"
            onClick={send}
            disabled={setting.tier === "live"}
            startIcon={<Iconify icon="solar:play-linear" width={14} />}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Send
          </Button>
          <Button
            size="small"
            disabled={!log.length}
            onClick={() => {
              setLog([]);
              setWrites(0);
            }}
            sx={{ color: "text.secondary" }}
          >
            Reset preview
          </Button>
        </Stack>
      </Stack>
      {log.map((r, i) => (
        <Box
          key={i}
          sx={{
            mt: 1,
            p: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {r.error ? (
            <Typography sx={{ typography: "s3", color: "status.danger.fg" }}>
              {r.error}
            </Typography>
          ) : (
            <>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary" }}
              >{`${r.tier} · ${JSON.stringify(r.request)}`}</Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  whiteSpace: "pre-wrap",
                }}
              >
                {JSON.stringify(r.response)}
              </Typography>
              {r.change && (
                <Typography
                  sx={{ typography: "s3", color: "text.secondary" }}
                >{`State change: ${r.change}`}</Typography>
              )}
              {r.seesWrite && (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  This read sees the write made earlier in this preview.
                </Typography>
              )}
            </>
          )}
        </Box>
      ))}
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        {setting.tier === "live"
          ? "Live tools aren't sent from a preview — it would reach the real service."
          : "Preview only — Reset puts the world back on its seed. Completed runs keep their evidence."}
      </Typography>
    </Box>
  );
}

ToolTryPanel.propTypes = {
  tool: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
};
