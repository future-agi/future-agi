import PropTypes from "prop-types";
import { Box, ButtonBase, Radio, Stack, Typography } from "@mui/material";
import { ROLES } from "./sources.constants";

const READS = [
  { id: "agent", label: "Yes, it reads the file directly" },
  { id: "tool", label: "No, only what the world's tools return" },
];

// For records and grader answers who reads it isn't a choice, so it's said.
const READ_FACT = {
  seed_records: "The agent sees these rows only through the world's tools.",
  evaluation_reference:
    "Never reaches the agent or the customer. Only the grader reads it.",
};

function Question({ children }) {
  return (
    <Typography sx={{ typography: "s1", fontWeight: 500, mb: 0.5 }}>
      {children}
    </Typography>
  );
}

Question.propTypes = { children: PropTypes.node };

export function Option({ checked, disabled, onPick, label, note, name }) {
  return (
    <Stack
      component="label"
      direction="row"
      spacing={1}
      alignItems="flex-start"
      sx={{
        py: 0.75,
        px: 1,
        mx: -1,
        borderRadius: "6px",
        cursor: disabled ? "default" : "pointer",
        "&:hover": disabled ? undefined : { bgcolor: "action.hover" },
      }}
    >
      <Radio
        size="small"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={onPick}
        sx={{ p: 0, mt: "1px" }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ typography: "s1", fontWeight: checked ? 500 : 400 }}>
          {label}
        </Typography>
        {note && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {note}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

Option.propTypes = {
  checked: PropTypes.bool,
  disabled: PropTypes.bool,
  onPick: PropTypes.func,
  label: PropTypes.node,
  note: PropTypes.string,
  name: PropTypes.string,
};

function Line({ label, value, extra }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="baseline" sx={{ py: 0.5 }}>
      <Typography
        sx={{
          typography: "s1",
          color: "text.tertiary",
          width: 96,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
        {value}
      </Typography>
      {extra}
    </Stack>
  );
}

Line.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  extra: PropTypes.node,
};

/**
 * How a file is used and whether the agent reads it, asked as two plain
 * questions with every option in view. Compact shows the two answers, and
 * Change opens the questions.
 */
export default function SourceUsage({
  role,
  visibility,
  onRole,
  onVisibility,
  compact = false,
  onToggle,
  disabled = false,
}) {
  const toggle = onToggle && !disabled && (
    <ButtonBase
      onClick={onToggle}
      sx={{
        typography: "s1",
        fontWeight: 500,
        flexShrink: 0,
        textDecoration: "underline",
        textUnderlineOffset: 3,
        textDecorationColor: (theme) => theme.palette.text.disabled,
        "&:hover": { textDecorationColor: "currentColor" },
      }}
    >
      {compact ? "Change" : "Done"}
    </ButtonBase>
  );

  if (compact) {
    const use = ROLES.find((one) => one.id === role);
    const read =
      READ_FACT[role] || READS.find((one) => one.id === visibility)?.label;
    return (
      <Box>
        <Line label="Used as" value={use?.option || "—"} extra={toggle} />
        <Line label="Agent reads it" value={read || "—"} />
      </Box>
    );
  }

  return (
    <Stack spacing={1.5}>
      <Box role="radiogroup" aria-label="How the builder uses this file">
        <Stack direction="row" alignItems="baseline">
          <Box sx={{ flex: 1 }}>
            <Question>How should the builder use this file?</Question>
          </Box>
          {toggle}
        </Stack>
        {ROLES.map((one) => (
          <Option
            key={one.id}
            name="source-use"
            checked={role === one.id}
            disabled={disabled}
            onPick={() => onRole(one.id)}
            label={one.option}
            note={one.note}
          />
        ))}
      </Box>
      <Box role="radiogroup" aria-label="Can the agent read it">
        <Question>Can the agent read it?</Question>
        {READ_FACT[role] ? (
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {READ_FACT[role]}
          </Typography>
        ) : (
          READS.map((one) => (
            <Option
              key={one.id}
              name="source-read"
              checked={visibility === one.id}
              disabled={disabled}
              onPick={() => onVisibility(one.id)}
              label={one.label}
            />
          ))
        )}
      </Box>
    </Stack>
  );
}

SourceUsage.propTypes = {
  role: PropTypes.string,
  visibility: PropTypes.string,
  onRole: PropTypes.func.isRequired,
  onVisibility: PropTypes.func.isRequired,
  // Two answer lines instead of the questions; onToggle switches.
  compact: PropTypes.bool,
  onToggle: PropTypes.func,
  disabled: PropTypes.bool,
};
