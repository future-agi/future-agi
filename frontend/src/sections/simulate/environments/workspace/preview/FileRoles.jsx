import PropTypes from "prop-types";
import { MenuItem, Stack, TextField, Typography } from "@mui/material";

import { useSetFileRole } from "src/api/simulate-environments/versions";
import { FILE_ROLE_LABELS, PREVIEW_COPY } from "./preview.constants";

// Each attached file with its role, and a pick to change it.
export default function FileRoles({ envId, files, roles, revision, locked }) {
  const setRole = useSetFileRole(envId);
  const paths = Array.from(
    new Set([...(files ?? []), ...Object.keys(roles ?? {})]),
  )
    .filter((path) => path.startsWith("knowledge/"))
    .sort();
  if (!paths.length) return null;
  return (
    <Stack spacing={1} data-testid="file-roles">
      <Typography sx={{ typography: "s2", color: "text.primary" }}>
        {PREVIEW_COPY.filesTitle}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {PREVIEW_COPY.filesHint}
      </Typography>
      {paths.map((path) => (
        <TextField
          key={path}
          select
          size="small"
          label={path.replace("knowledge/", "")}
          value={roles?.[path] ?? ""}
          disabled={locked || setRole.isPending}
          onChange={(event) =>
            setRole.mutate({ path, role: event.target.value, revision })
          }
        >
          <MenuItem value="" disabled>
            {PREVIEW_COPY.noRole}
          </MenuItem>
          {Object.entries(FILE_ROLE_LABELS).map(([value, label]) => (
            <MenuItem key={value} value={value}>
              {label}
            </MenuItem>
          ))}
        </TextField>
      ))}
      {setRole.error?.message && (
        <Typography role="alert" sx={{ typography: "s3", color: "error.main" }}>
          {setRole.error.message}
        </Typography>
      )}
    </Stack>
  );
}

FileRoles.propTypes = {
  envId: PropTypes.string,
  files: PropTypes.array,
  roles: PropTypes.object,
  revision: PropTypes.number,
  locked: PropTypes.bool,
};
