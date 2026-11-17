import PropTypes from "prop-types";
import { Box, ButtonBase, Radio, Stack, Typography } from "@mui/material";
import { ROLES } from "../../_mock/orgData";

/*
  How a file is used, asked as plain questions with every option in view
  (design system: a choice saved with the form is a radio group). The roles
  are data.md's — policy, reference, records, evaluator-only — said as what
  happens to the file. Whether the agent reads it is asked only where it's a
  real choice; for records and grader-only answers it's a fact, so it's said.
*/
/* The same words the Sources table and notices use (orgData ROLES). */
const USES = ROLES.map((r) => ({ id: r.id, label: r.option, note: r.note }));

const READS = [
  { id: "agent", label: "Yes — it reads the file directly" },
  { id: "tool", label: "No — only what the world's tools return" },
];

/* Where reading isn't a choice, what's true instead. */
const READ_FACT = {
  seed: "The agent sees these rows only through the world's tools.",
  "evaluation-reference":
    "Never reaches the agent or the customer — only the grader reads it.",
};

function Question({ children }) {
  return (
    <Typography
      sx={{ typography: "s1", fontWeight: 500, color: "text.primary", mb: 0.5 }}
    >
      {children}
    </Typography>
  );
}
Question.propTypes = { children: PropTypes.node };

function Option({ checked, disabled, onPick, label, note, name }) {
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
  label: PropTypes.string,
  note: PropTypes.string,
  name: PropTypes.string,
};

export default function SourceUsage({
  role,
  visibility,
  disabled = false,
  onRole,
  onVisibility,
  compact = false,
  onToggle,
}) {
  const toggle = onToggle && !disabled && (
    <ButtonBase
      onClick={onToggle}
      sx={{
        typography: "s1",
        fontWeight: 500,
        color: "text.primary",
        textDecoration: "underline",
        textUnderlineOffset: 3,
        textDecorationColor: (t) => t.palette.text.disabled,
        flexShrink: 0,
        "&:hover": { textDecorationColor: "currentColor" },
      }}
    >
      {compact ? "Change" : "Done"}
    </ButtonBase>
  );
  /* Compact: the two answers as key-value lines; Change opens the options. */
  if (compact) {
    const use = USES.find((u) => u.id === role);
    const read =
      READ_FACT[role] || READS.find((r) => r.id === visibility)?.label || "—";
    const row = (k, v, extra) => (
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="baseline"
        sx={{ py: 0.5 }}
      >
        <Typography
          sx={{
            typography: "s1",
            color: "text.tertiary",
            width: 96,
            flexShrink: 0,
          }}
        >
          {k}
        </Typography>
        <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
          {v}
        </Typography>
        {extra}
      </Stack>
    );
    return (
      <Box>
        {row("Used as", use?.label || "—", toggle)}
        {row("Agent reads it", read)}
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
        {USES.map((u) => (
          <Option
            key={u.id}
            name="source-use"
            checked={role === u.id}
            disabled={disabled}
            onPick={() => onRole(u.id)}
            label={u.label}
            note={u.note}
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
          READS.map((r) => (
            <Option
              key={r.id}
              name="source-read"
              checked={visibility === r.id}
              disabled={disabled}
              onPick={() => onVisibility(r.id)}
              label={r.label}
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
  disabled: PropTypes.bool,
  onRole: PropTypes.func.isRequired,
  onVisibility: PropTypes.func.isRequired,
  /* Two summary lines instead of every option; onToggle opens them. */
  compact: PropTypes.bool,
  onToggle: PropTypes.func,
};
