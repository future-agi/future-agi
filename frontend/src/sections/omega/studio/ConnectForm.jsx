import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { FRAME_CLASS } from "src/sections/omega/embed";

import { CONNECT, LINK_PROBLEM_COPY } from "./studio-copy";

// The paste field. The link it takes carries a key, so the field is kept out
// of autocapture, session replay and the browser's form history, and is
// emptied the moment a connection starts.
export default function ConnectForm({ onConnect }) {
  const [value, setValue] = useState("");
  const [problem, setProblem] = useState(null);

  const submit = (event) => {
    event.preventDefault();
    const result = onConnect(value);
    if (result?.ok) {
      setValue("");
      setProblem(null);
    } else {
      setProblem(result?.problem || "invalid");
    }
  };

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
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (problem) setProblem(null);
          }}
          placeholder={CONNECT.placeholder}
          size="small"
          fullWidth
          error={Boolean(problem)}
          helperText={problem ? LINK_PROBLEM_COPY[problem] : undefined}
          className={FRAME_CLASS}
          inputProps={{
            "aria-label": "Link from omega dev",
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
};
