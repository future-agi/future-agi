import { useState } from "react";
import { Box, Collapse, Stack } from "@mui/material";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { Field } from "../formBits";
import TextLink from "../shared/TextLink";

const MAX_SESSIONS = 100;

const isJson = (text) => {
  if (!text.trim()) return true;
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
};

/**
 * LiveKit's room config and how many sessions may run at once, behind a
 * text link. The build API takes neither yet, so they're flagged UI only and
 * never sent: the form stays live.
 */
export default function LiveKitRoomConfig() {
  const [open, setOpen] = useState(false);
  const [roomConfig, setRoomConfig] = useState("");
  const [maxSessions, setMaxSessions] = useState("5");
  const jsonOk = isJson(roomConfig);
  const sessions = Number(maxSessions);
  const sessionsOk =
    Number.isInteger(sessions) && sessions >= 1 && sessions <= MAX_SESSIONS;

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <TextLink onClick={() => setOpen((o) => !o)}>
          {open
            ? "Hide room config and sessions"
            : "Room config and sessions at once"}
        </TextLink>
        <UiOnlyFlag />
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.5} sx={{ mt: 1.25 }}>
          <Field
            label="Room config JSON"
            mono
            multiline
            minRows={3}
            value={roomConfig}
            onChange={setRoomConfig}
            placeholder='{"metadata": {"locale": "en-US"}}'
            error={jsonOk ? null : "Not valid JSON."}
            helper="Optional. Passed to every test room the agent is dispatched to."
          />
          <Field
            label="Max sessions at once"
            mono
            value={maxSessions}
            onChange={(v) => setMaxSessions(v.replace(/\D/g, "").slice(0, 3))}
            inputProps={{ inputMode: "numeric" }}
            error={sessionsOk ? null : `Between 1 and ${MAX_SESSIONS}.`}
            helper="LiveKit Cloud: your plan's concurrent session limit (Settings → Project). Self-hosted: what your workers can take."
          />
        </Stack>
      </Collapse>
    </Box>
  );
}
