import PropTypes from "prop-types";
import { Alert, MenuItem, Stack, TextField, Typography } from "@mui/material";

import { useSetFileRole } from "src/api/simulate-environments/versions";
import SectionCard from "../../components/SectionCard";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.fileRoles;
const PREFIX = "knowledge/";

// Every attached file, readable or evaluator-only: the cast lists evaluator-only files only under `roles`.
const attachedFiles = (sources) =>
  [
    ...new Set([
      ...(sources?.files ?? []),
      ...Object.keys(sources?.roles ?? {}),
    ]),
  ]
    .filter((path) => path.startsWith(PREFIX))
    .sort();

// Each attached file with the role the builder uses it in, and a pick to change it.
export default function FileRoles({ envId, sources, revision, locked }) {
  const setRole = useSetFileRole(envId);
  const paths = attachedFiles(sources);
  if (!paths.length) return null;
  const roles = sources.roles ?? {};
  const seedsUsed = Object.values(roles).includes("seed_records");
  return (
    <SectionCard title={C.title} subtitle={C.subtitle} sx={{ mb: 2 }}>
      <Stack spacing={1.5} sx={{ p: 1.5 }} data-testid="file-roles">
        {paths.map((path) => (
          <TextField
            key={path}
            select
            size="small"
            label={path.slice(PREFIX.length)}
            value={roles[path] ?? ""}
            disabled={locked || setRole.isPending}
            // A file with no role yet says so instead of showing an empty field.
            SelectProps={{ displayEmpty: true }}
            InputLabelProps={{ shrink: true }}
            onChange={(event) =>
              setRole.mutate({ path, role: event.target.value, revision })
            }
            sx={{ maxWidth: 420 }}
          >
            <MenuItem value="" disabled>
              {C.noRole}
            </MenuItem>
            {Object.entries(C.roles).map(([value, label]) => (
              <MenuItem key={value} value={value}>
                {label}
              </MenuItem>
            ))}
          </TextField>
        ))}
        {seedsUsed && sources.seed_records_loaded === false && (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {C.seedContextOnly}
          </Typography>
        )}
        {setRole.error && (
          <Alert severity="error" onClose={() => setRole.reset()}>
            {refusalText(setRole.error, C.failed)}
          </Alert>
        )}
      </Stack>
    </SectionCard>
  );
}

FileRoles.propTypes = {
  envId: PropTypes.string.isRequired,
  // The cast's knowledge_sources: readable files, and every attached file's role by path.
  sources: PropTypes.shape({
    files: PropTypes.arrayOf(PropTypes.string),
    roles: PropTypes.objectOf(PropTypes.string),
    seed_records_loaded: PropTypes.bool,
  }),
  // The cast revision the pick is made on.
  revision: PropTypes.number,
  locked: PropTypes.bool,
};
