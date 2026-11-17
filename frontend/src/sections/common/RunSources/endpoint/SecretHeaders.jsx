// One widget for an endpoint's headers (options-truth L5 G5, W0-6): each
// header takes its value from a workspace secret picked from the list; the
// HTTP card and the HTTP endpoint source both use it. W6 B1: the picker is
// the one SecretSelect, which flags a name the workspace does not have.
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SecretSelect from "./SecretSelect";
import { useSecretNamesState } from "./useSecretNamesState";

/** The workspace's secret names (GET secrets, first page). */
export function useSecretNames() {
  return useSecretNamesState() ?? [];
}

export default function SecretHeaders({ headers, onChange, testIdPrefix }) {
  const secrets = useSecretNamesState();
  const list = headers ?? [];
  const set = (i, patch) =>
    onChange(list.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  return (
    <Stack spacing={0.75} data-testid="secret-headers">
      {list.map((h, i) => (
        <Stack key={`h-${i}`} direction="row" alignItems="center" gap={1}>
          <TextField
            size="small"
            value={h.name ?? ""}
            placeholder="Header name"
            onChange={(e) => set(i, { name: e.target.value })}
            inputProps={{
              "aria-label": "Header name",
              ...(testIdPrefix
                ? { "data-testid": `${testIdPrefix}-name-${i}` }
                : {}),
            }}
            sx={{ width: 180, "& input": { py: 0.5, typography: "s2" } }}
          />
          <Typography typography="s2" color="text.secondary">
            from secret
          </Typography>
          <SecretSelect
            value={h.secret ?? ""}
            names={secrets}
            onChange={(v) => set(i, { secret: v })}
            testId={testIdPrefix ? `${testIdPrefix}-secret-${i}` : undefined}
          />
          <Button
            size="small"
            onClick={() => onChange(list.filter((_, j) => j !== i))}
            sx={{ minWidth: 0, typography: "s2" }}
          >
            Remove
          </Button>
        </Stack>
      ))}
      <Box>
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:plus" width={14} />}
          onClick={() => onChange([...list, { name: "", secret: "" }])}
          data-testid={testIdPrefix ? `${testIdPrefix}-add` : undefined}
          sx={{ typography: "s2" }}
        >
          Add header
        </Button>
      </Box>
    </Stack>
  );
}

SecretHeaders.propTypes = {
  headers: PropTypes.arrayOf(PropTypes.object),
  onChange: PropTypes.func.isRequired,
  testIdPrefix: PropTypes.string,
};
