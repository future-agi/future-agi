import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

import { PREVIEW_COPY } from "./preview.constants";

// The cases a rebuild dropped, with why, and the ones back under their keys, with whether proved again.
export default function DroppedCases({ dropped, restored }) {
  if (!dropped?.length && !restored?.length) return null;
  return (
    <Stack spacing={0.5} sx={{ px: 2, py: 1 }} data-testid="dropped-cases">
      {dropped?.length > 0 && (
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          {PREVIEW_COPY.droppedTitle}
        </Typography>
      )}
      {dropped?.map((one) => (
        <Typography
          key={one.key}
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {one.name}: {one.reason}
          {PREVIEW_COPY.droppedFor(one.agent_version)}
          {one.restore_failed
            ? ` · ${PREVIEW_COPY.restoreFailed(one.restore_failed)}`
            : ""}
        </Typography>
      ))}
      {restored?.length > 0 && (
        <Typography sx={{ typography: "s2", color: "text.primary" }}>
          {PREVIEW_COPY.restoredTitle}
        </Typography>
      )}
      {restored?.map((one) => (
        <Typography
          key={one.key}
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {one.name}:{" "}
          {one.proved
            ? PREVIEW_COPY.restoredProved
            : PREVIEW_COPY.restoredUnproved}
        </Typography>
      ))}
    </Stack>
  );
}

DroppedCases.propTypes = {
  dropped: PropTypes.array,
  restored: PropTypes.array,
};
