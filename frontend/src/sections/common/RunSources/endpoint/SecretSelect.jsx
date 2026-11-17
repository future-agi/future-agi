// The one workspace-secret picker of the endpoint sources (W6 B1/B4): HTTP
// headers, A2A auth and the MCP server's env all pick a secret by name from
// the workspace list. A name that is not on the list (a draft from before,
// or a typo) is shown in red with the copy that blocks Test and Run.
import React from "react";
import PropTypes from "prop-types";
import { MenuItem, Select, Stack, Typography } from "@mui/material";
import { missingSecretMessage } from "./requestModel";
import { useSecretNamesState } from "./useSecretNamesState";

export default function SecretSelect({
  value,
  onChange,
  names,
  label = "Secret",
  testId,
  minWidth = 200,
}) {
  const loaded = useSecretNamesState();
  const list = names ?? loaded ?? [];
  const known = !value || !(names ?? loaded) || list.includes(value);
  return (
    <Stack spacing={0.25}>
      <Select
        size="small"
        value={value ?? ""}
        displayEmpty
        error={!known}
        onChange={(e) => onChange(e.target.value)}
        sx={{ height: 30, typography: "s2", minWidth }}
        inputProps={{
          "aria-label": label,
          ...(testId ? { "data-testid": testId } : {}),
        }}
      >
        <MenuItem value="" sx={{ typography: "s2" }}>
          Pick a workspace secret
        </MenuItem>
        {[...new Set([...list, ...(value ? [value] : [])])].map((s) => (
          <MenuItem
            key={s}
            value={s}
            sx={{ typography: "s2", fontFamily: "monospace" }}
          >
            {s}
          </MenuItem>
        ))}
      </Select>
      {!known ? (
        <Typography
          typography="s3"
          color="error.main"
          role="alert"
          data-testid={testId ? `${testId}-missing` : undefined}
        >
          {missingSecretMessage([value])}
        </Typography>
      ) : null}
    </Stack>
  );
}

SecretSelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  /** The workspace names when the caller already has them. */
  names: PropTypes.arrayOf(PropTypes.string),
  label: PropTypes.string,
  testId: PropTypes.string,
  minWidth: PropTypes.number,
};
