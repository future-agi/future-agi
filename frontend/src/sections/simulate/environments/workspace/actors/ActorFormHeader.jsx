import PropTypes from "prop-types";
import { Alert, Box, Typography } from "@mui/material";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
const F = C.form;
const ALERT = { mx: 2.5, mt: 1.5, flexShrink: 0 };

export default function ActorFormHeader({
  actor,
  error,
  offline,
  conflict,
}) {
  const existing = Boolean(actor.id);
  const sideLabel = C.sides[actor.side]?.label;
  return (
    <>
      <Box
        sx={{
          px: 2.5,
          py: 2,
          // Clears the drawer's own close button.
          pr: 6,
          flexShrink: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
          {existing ? F.editTitle(actor.name || actor.id) : F.createTitle}
        </Typography>
        {!existing && sideLabel && (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {F.createSubtitle(sideLabel)}
          </Typography>
        )}
      </Box>
      {error && (
        <Alert severity="error" sx={ALERT}>
          {error}
        </Alert>
      )}
      {offline && (
        <Alert severity="info" sx={ALERT}>
          {C.offline}
        </Alert>
      )}
      {conflict && (
        <Alert severity="warning" sx={ALERT}>
          {conflict === "removed" ? F.removedElsewhere : F.changedElsewhere}
        </Alert>
      )}
    </>
  );
}

ActorFormHeader.propTypes = {
  // `{side}` for a new actor.
  actor: PropTypes.object.isRequired,
  error: PropTypes.string,
  offline: PropTypes.bool,
  conflict: PropTypes.oneOf(["changed", "removed"]),
};
