import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

/**
 * What one actor sees before a run, and what it never sees (TH-8927): the
 * effective view of the actor as it is in the draft. Two short key-value
 * lists — the evaluator's material is always on the second.
 */
export default function ActorSees({ view, name }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Group title={`${name || "This actor"} sees`}>
        {view.sees.map((r) => (
          <Row key={r.label} label={r.label} text={r.text} />
        ))}
      </Group>
      <Group title="Never sees" divided>
        {view.never.map((t) => (
          <Typography
            key={t}
            sx={{ typography: "s3", color: "text.secondary", py: 0.25 }}
          >
            {t}
          </Typography>
        ))}
      </Group>
    </Box>
  );
}
ActorSees.propTypes = {
  view: PropTypes.shape({
    sees: PropTypes.array.isRequired,
    never: PropTypes.array.isRequired,
  }).isRequired,
  name: PropTypes.string,
};

function Group({ title, divided = false, children }) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 1,
        ...(divided && { borderTop: "1px solid", borderColor: "divider" }),
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Group.propTypes = {
  title: PropTypes.string,
  divided: PropTypes.bool,
  children: PropTypes.node,
};

function Row({ label, text }) {
  return (
    <Stack direction="row" spacing={1.5} sx={{ py: 0.25 }}>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 88,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
      >
        {text}
      </Typography>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, text: PropTypes.string };
