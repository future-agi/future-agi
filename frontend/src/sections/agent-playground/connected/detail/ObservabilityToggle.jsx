import PropTypes from "prop-types";
import React, { useState } from "react";
import {
  Box,
  CircularProgress,
  Skeleton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useSetObservability } from "src/api/agents/agentDetail";
import { useAuthContext } from "src/auth/hooks";
import CustomTooltip from "src/components/tooltip";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { LineSkeleton } from "./bits";

/**
 * Observability on/off for the agent, from its observability_enabled. A
 * refused change snaps back and says why.
 */
export default function ObservabilityToggle({ agent }) {
  const [override, setOverride] = useState(null);
  const [error, setError] = useState(null);
  const mutation = useSetObservability(agent.id);
  // Same gate as the old agent pages: viewers can't edit a simulation agent.
  const { role } = useAuthContext();
  const canEdit = !!RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const locked = !canEdit || mutation.isPending;

  const checked = override ?? !!agent.observabilityEnabled;
  const change = (next) => {
    setOverride(next);
    setError(null);
    mutation.mutate(
      { enabled: next },
      {
        // The refetched agent is the truth once the change is accepted.
        onSuccess: () => setOverride(null),
        onError: (e) => {
          setOverride(null);
          setError(e?.message || "Couldn't change observability");
        },
      },
    );
  };

  const caption = checked
    ? "Tracking this agent's calls and logs in Observe."
    : "Turn on to track calls, monitor logs and debug this agent.";

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* The switch sits next to its label, and the label toggles it too. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ pl: 1, pr: 2, minHeight: 44 }}
      >
        <Stack
          component="label"
          direction="row"
          alignItems="center"
          sx={{
            flexShrink: 0,
            userSelect: "none",
            cursor: locked ? "default" : "pointer",
          }}
        >
          <CustomTooltip
            show={!canEdit}
            title="You don't have permission to change this"
            placement="top"
            arrow
            size="small"
          >
            <span>
              <Switch
                size="small"
                checked={checked}
                disabled={locked}
                onChange={(e) => change(e.target.checked)}
                inputProps={{ "aria-label": "Observability" }}
              />
            </span>
          </CustomTooltip>
          <Typography sx={{ typography: "s2", fontWeight: 600, ml: 0.5 }}>
            Observability
          </Typography>
        </Stack>
        {mutation.isPending && (
          <CircularProgress size={12} aria-label="Saving" />
        )}
        <Typography
          noWrap
          title={caption}
          sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
        >
          {caption}
        </Typography>
      </Stack>
      {error && (
        <Stack
          role="alert"
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1,
            borderTop: "1px solid",
            borderColor: (t) =>
              alpha("#DC2626", t.palette.mode === "dark" ? 0.35 : 0.25),
            bgcolor: (t) =>
              alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Iconify
            icon="solar:danger-circle-linear"
            width={16}
            sx={{ color: "#DC2626", mt: 0.125, flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            {error}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

ObservabilityToggle.propTypes = { agent: PropTypes.object.isRequired };

/** The toggle's row while the agent loads: same box, label and height. */
export function ObservabilityToggleSkeleton() {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ pl: 1, pr: 2, minHeight: 44 }}
      >
        <Stack direction="row" alignItems="center" sx={{ flexShrink: 0 }}>
          <Skeleton
            variant="rounded"
            width={34}
            height={20}
            sx={{ mx: 0.75, borderRadius: 10 }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 600, ml: 0.5 }}>
            Observability
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s3" }}>
          <LineSkeleton width={260} />
        </Typography>
      </Stack>
    </Box>
  );
}
