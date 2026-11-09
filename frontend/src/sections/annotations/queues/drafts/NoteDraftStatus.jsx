import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { NOTE_DRAFT_TICK_MS, draftStatusText } from "./noteDraft";

/**
 * The line under Notes (KH-SCOPE §6 B2): `Draft saved · just now`, then
 * `· 2 min ago`; `Restored your unsaved note` after a reload put it back.
 * Renders nothing when there is no draft. `draft` = useNoteDraft's result.
 */
export default function NoteDraftStatus({ draft, sx }) {
  const [now, setNow] = useState(() => Date.now());
  const ticking = draft?.status === "saved";
  useEffect(() => {
    if (!ticking) return undefined;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), NOTE_DRAFT_TICK_MS);
    return () => clearInterval(id);
  }, [ticking, draft?.savedAt]);
  const text = draftStatusText(draft ?? {}, Math.max(now, draft?.savedAt ?? 0));
  if (!text) return null;
  return (
    <Typography
      typography="s3"
      color={draft.status === "unavailable" ? "warning.main" : "text.secondary"}
      data-testid="note-draft-status"
      aria-live="polite"
      sx={{ display: "block", mt: 0.5, ...sx }}
    >
      {text}
    </Typography>
  );
}

NoteDraftStatus.propTypes = {
  draft: PropTypes.shape({
    status: PropTypes.oneOf(["idle", "saved", "restored", "unavailable"]),
    savedAt: PropTypes.number,
  }),
  sx: PropTypes.object,
};
