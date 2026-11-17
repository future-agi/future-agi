// IT-R2 G4 P4 · the prompt's variables, edited in place under the messages
// (single click, no full-screen overlay): one row per `{{name}}` with its
// value (row 1 of the variable data). A variable with no value reads
// `no value yet` in grey (never red). Values save on blur / Enter (and
// after a short pause while typing). Several rows of data (imported or
// added in the Variables drawer) keep the drawer one click away:
// `Row 1 of 3 · Edit all rows`.
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, InputBase, Stack, Typography } from "@mui/material";

const firstOf = (v) => (Array.isArray(v) ? v[0] : v);
const hasValue = (v) =>
  (Array.isArray(v) ? v : [v]).some((x) => x != null && String(x).trim());

function VariableRow({ name, value, onCommit, disabled }) {
  const [draft, setDraft] = useState(value ?? "");
  const timer = useRef(null);
  const latest = useRef(value ?? "");
  useEffect(() => {
    // An outside change (drawer, import, a reload) wins over the draft.
    if ((value ?? "") !== latest.current) {
      latest.current = value ?? "";
      setDraft(value ?? "");
    }
  }, [value]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const commit = (next) => {
    clearTimeout(timer.current);
    if (next === latest.current) return;
    latest.current = next;
    onCommit(next);
  };
  const filled = String(draft).trim() !== "";
  return (
    <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
      <Box
        component="span"
        title={filled ? `{{${name}}}` : `{{${name}}} has no value yet`}
        sx={{
          flexShrink: 0,
          px: 0.75,
          borderRadius: 0.5,
          typography: "s2",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          color: filled ? "success.dark" : "text.secondary",
          bgcolor: filled ? "success.lighter" : "background.neutral",
          maxWidth: 220,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {`{{${name}}}`}
      </Box>
      <InputBase
        value={draft}
        disabled={disabled}
        placeholder="no value yet"
        inputProps={{
          "aria-label": `Value of ${name}`,
          "data-testid": `variable-value-${name}`,
        }}
        onChange={(e) => {
          const next = e.target.value;
          setDraft(next);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => commit(next), 700);
        }}
        onBlur={() => commit(draft)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          }
        }}
        sx={{
          flex: 1,
          minWidth: 0,
          typography: "s2",
          px: 1,
          height: 28,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          "&.Mui-focused": { borderColor: "primary.main" },
          "& input::placeholder": { color: "text.disabled", opacity: 1 },
        }}
      />
    </Stack>
  );
}
VariableRow.propTypes = {
  name: PropTypes.string.isRequired,
  value: PropTypes.string,
  onCommit: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default function VariableValuesInline({
  names,
  variableData,
  setVariableData,
  onOpenAll,
  disabled,
}) {
  if (!names?.length) return null;
  const rows = Math.max(
    1,
    ...names.map((n) =>
      Array.isArray(variableData?.[n]) ? variableData[n].length : 1,
    ),
  );
  const missing = names.filter((n) => !hasValue(variableData?.[n])).length;
  const set = (name, value) => {
    const next = { ...(variableData ?? {}) };
    names.forEach((n) => {
      if (!(n in next)) next[n] = [""];
    });
    const col = Array.isArray(next[name]) ? [...next[name]] : [next[name]];
    col[0] = value;
    next[name] = col;
    setVariableData(next);
  };
  return (
    <Stack
      gap={0.75}
      data-testid="prompt-variables-line"
      sx={{
        mt: 1.25,
        p: 1.25,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.default",
      }}
    >
      <Stack direction="row" alignItems="baseline" gap={1}>
        <Typography typography="s2" fontWeight="fontWeightMedium">
          Variables
        </Typography>
        <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
          {missing
            ? `${missing} without a value · the run leaves ${missing > 1 ? "them" : "it"} blank`
            : rows > 1
              ? `Row 1 of ${rows}`
              : "Values used when you run"}
        </Typography>
        {onOpenAll ? (
          <Box
            component="button"
            type="button"
            onClick={onOpenAll}
            sx={{
              all: "unset",
              cursor: "pointer",
              typography: "s3",
              color: "primary.main",
              "&:hover": { textDecoration: "underline" },
              "&:focus-visible": { outline: "2px solid" },
            }}
          >
            {rows > 1 ? "Edit all rows" : "Add rows or import"}
          </Box>
        ) : null}
      </Stack>
      {names.map((name) => (
        <VariableRow
          key={name}
          name={name}
          value={String(firstOf(variableData?.[name]) ?? "")}
          onCommit={(v) => set(name, v)}
          disabled={disabled}
        />
      ))}
    </Stack>
  );
}

VariableValuesInline.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string),
  variableData: PropTypes.object,
  setVariableData: PropTypes.func.isRequired,
  onOpenAll: PropTypes.func,
  disabled: PropTypes.bool,
};
