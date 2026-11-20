import PropTypes from "prop-types";
import {
  Box,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { PICK_ROLES, pickedRoleOf } from "../../_mock/orgData";

/* Right after an upload: each file's role, picked here (handover — the
   server's policy, seed_records, evaluator_only). The builder's guess is
   pre-picked; the file's drawer can change it later. */
export default function UploadRoles({ sources, onRole, disabled }) {
  if (!sources?.length) return null;
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          px: 2.5,
          pt: 1.5,
          pb: 0.5,
        }}
      >
        {sources.length === 1 ? "What this file is" : "What each file is"}
      </Typography>
      {sources.map((s) => (
        <Box
          key={s.id}
          sx={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) auto",
            alignItems: "center",
            columnGap: 1.5,
            px: 2.5,
            py: 0.875,
          }}
        >
          <Typography noWrap sx={{ typography: "s1", fontWeight: 500 }}>
            {s.name}
          </Typography>
          <ToggleButtonGroup
            exclusive
            size="small"
            disabled={disabled}
            value={pickedRoleOf(s.role)}
            onChange={(_, v) => v && v !== pickedRoleOf(s.role) && onRole(s, v)}
            aria-label={`Role of ${s.name}`}
          >
            {PICK_ROLES.map((r) => (
              <Tooltip key={r.id} arrow title={r.note}>
                <ToggleButton value={r.id} sx={{ px: 1, py: 0.25 }}>
                  {r.label}
                </ToggleButton>
              </Tooltip>
            ))}
          </ToggleButtonGroup>
        </Box>
      ))}
    </Box>
  );
}

UploadRoles.propTypes = {
  sources: PropTypes.array,
  onRole: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
