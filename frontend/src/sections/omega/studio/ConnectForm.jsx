import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { FRAME_CLASS } from "src/sections/omega/embed";

import { LINK_PROBLEM } from "./connect-link";
import { CONNECT, LINK_PROBLEM_COPY } from "./studio-copy";

// What the field says about a link it cannot use. omega dev prints its local
// link as "#t=<key>" when its output isn't a terminal; that line has no key,
// and only its "Open in Future AGI" line does.
const PROBLEM_COPY = {
  ...LINK_PROBLEM_COPY,
  [LINK_PROBLEM.placeholderKey]:
    "This link shows <key> instead of the key, because omega dev leaves the key out when its output isn't a terminal. Paste its “Open in Future AGI” line instead, or run omega dev in a terminal.",
};

// The paste field. The link it takes carries a key, so the field is kept out
// of autocapture, session replay and the browser's form history, and is
// emptied the moment a connection starts.
//
// Taking a paste is this screen's one job, so the field has focus, with its
// text selected, whenever the screen appears, whenever `focusKey` changes (a
// new notice) and after a link is refused: ⌘V and Enter are all it takes.
// `describedBy` names the notice that says why the screen is showing, so it
// is read with the field.
export default function ConnectForm({ onConnect, describedBy, focusKey }) {
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState(null);
  const inputRef = useRef(null);
  const fieldId = useId();
  const helperId = `${fieldId}-helper-text`;

  const focusField = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }, []);

  useEffect(() => {
    focusField();
  }, [focusField, focusKey]);

  const submit = (event) => {
    event.preventDefault();
    const result = onConnect(value);
    if (result?.ok) {
      setValue("");
      setProblem(null);
    } else {
      setProblem(result?.problem || "invalid");
      focusField();
    }
  };

  const describedByIds =
    [describedBy, problem ? helperId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <Box
      component="form"
      noValidate
      autoComplete="off"
      onSubmit={submit}
      data-testid="studio-connect-form"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: { xs: "column", sm: "row" },
          alignItems: { xs: "stretch", sm: "flex-start" },
          gap: 1,
        }}
      >
        <TextField
          id={fieldId}
          inputRef={inputRef}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (problem) setProblem(null);
          }}
          placeholder={CONNECT.placeholder}
          size="small"
          fullWidth
          error={Boolean(problem)}
          helperText={problem ? PROBLEM_COPY[problem] : undefined}
          className={FRAME_CLASS}
          inputProps={{
            "aria-label": "Link from omega dev",
            "aria-describedby": describedByIds,
            "data-testid": "studio-connect-input",
            autoComplete: "off",
            autoCorrect: "off",
            autoCapitalize: "off",
            spellCheck: false,
            "data-private": "true",
            "data-ph-no-capture": "true",
          }}
          sx={{
            "& input": {
              fontFamily:
                'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              fontSize: 13,
            },
          }}
        />
        <Button
          type="submit"
          variant="contained"
          color="primary"
          data-testid="studio-connect-submit"
          sx={{ flexShrink: 0, height: 40, px: 2.5 }}
        >
          {CONNECT.action}
        </Button>
      </Box>
      <Typography variant="s2" color="text.secondary">
        {CONNECT.localNetwork}
      </Typography>
    </Box>
  );
}

ConnectForm.propTypes = {
  onConnect: PropTypes.func.isRequired,
  describedBy: PropTypes.string,
  focusKey: PropTypes.shape({ kind: PropTypes.string }),
};
