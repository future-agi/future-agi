import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Who was on the call (TH-8651): the caller, anyone on the caller's side who
 * came in, and the agent. A sub-actor the scenario had but who never came in is
 * named after, so a quiet transcript isn't taken for a missing one.
 */
export function OnTheCall({ joined = [], missed = [] }) {
  return (
    <Box sx={{ px: 2, pt: 1.25 }}>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        <Box component="span" sx={{ color: "text.tertiary" }}>
          On the call:{" "}
        </Box>
        {["Caller", ...joined.map((j) => j.actor.name), "Agent"].join(" · ")}
        {missed.length > 0 && (
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {` — ${missed.map((a) => a.name).join(", ")} did not come in`}
          </Box>
        )}
      </Typography>
    </Box>
  );
}
OnTheCall.propTypes = {
  joined: PropTypes.array,
  missed: PropTypes.array,
};

/**
 * One turn by a sub-actor, between the caller's and the agent's: who they are,
 * that they are not the person being served, their role and how they joined.
 */
export function SubActorTurn({ line, time }) {
  return (
    <Box
      sx={{
        borderLeft: "2px dashed",
        borderColor: "text.disabled",
        ml: 2.5,
        px: 1.75,
        py: 1.25,
      }}
    >
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", fontFamily: MONO }}
      >
        {time} ·{" "}
        <Box component="span" sx={{ fontFamily: "inherit", fontWeight: 600 }}>
          {`${line.actor.name} (not the person being served)`}
        </Box>
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${line.role} · ${line.how}`}
        {!line.reached && " · did not reach the agent"}
      </Typography>
      <Typography sx={{ typography: "s1", mt: 0.25 }}>{line.text}</Typography>
    </Box>
  );
}
SubActorTurn.propTypes = {
  line: PropTypes.object.isRequired,
  time: PropTypes.string.isRequired,
};
