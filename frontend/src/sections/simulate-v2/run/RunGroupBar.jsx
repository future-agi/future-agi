import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Popover,
  MenuItem,
  ListItemText,
  Tooltip,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { groupMembersOf } from "../_mock/runGroup";
import { runIdentityOf } from "../_mock/runIdentity";
import { runResultOf } from "../_mock/runResult";
import { useSimStore } from "../store";
import { clickable, focusRing } from "../components/clickable";

/**
 * The run group this run belongs to, under the run's header.
 *
 * A group is one agent version run in several environments, so each member is
 * still its own run — agent v3 × env v2 in one environment, agent v3 × env v5
 * in another. This strip names the group, switches between its runs, and puts
 * their pass rates side by side: the same measure as the runs list (the mean
 * of each scenario's share of passing repeats), so a number here is the number
 * that run's row shows.
 *
 * Renders nothing for a run on its own.
 */
export default function RunGroupBar({ envId, runId, live = false }) {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const [anchor, setAnchor] = useState(null);

  const members = useMemo(() => {
    const self = (state.byEnv?.[envId]?.runs || []).find((r) => r.id === runId);
    const list = groupMembersOf(state, self);
    if (!list) return null;
    return list.map((m) => {
      const current = m.runId === runId;
      const finished = !!m.run?.finishedAt && !(current && live);
      const rate =
        finished && m.env ? runResultOf(m.env, m.envState, m.run)?.rate : null;
      return {
        ...m,
        current,
        identity: m.run ? runIdentityOf(m.run, m.envState, m.env).label : null,
        /* What the side-by-side reads: a rate once a run has one, and what
           it's doing until then. */
        result: !m.run
          ? "removed"
          : !finished
            ? "running"
            : rate == null
              ? "—"
              : `${rate}%`,
        missing: !m.run || !m.env,
      };
    });
  }, [state, envId, runId, live]);

  if (!members || members.length < 2) return null;

  const open = (m) => {
    setAnchor(null);
    if (m.current || m.missing) return;
    /* Switching runs within the group, not going somewhere new — Back still
       leaves the group the way it came in. */
    navigate(paths.dashboard.simulate.simulationRun(m.envId, m.runId), {
      replace: true,
    });
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2.5}
      sx={{
        px: 3,
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        minWidth: 0,
      }}
    >
      <ButtonBase
        type="button"
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.625,
          flexShrink: 0,
          height: 26,
          px: 0.875,
          borderRadius: "4px",
          font: "inherit",
          cursor: "pointer",
          border: "1px solid",
          borderColor: anchor ? "text.disabled" : "divider",
          bgcolor: anchor ? "action.hover" : "transparent",
          color: "text.primary",
          "&:hover": { bgcolor: "action.hover", borderColor: "text.disabled" },
        }}
      >
        <Iconify
          icon="solar:layers-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography noWrap sx={{ typography: "s3", fontWeight: 600 }}>
          Run group · {members.length} environments
        </Typography>
        <Iconify
          icon={
            anchor ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={12}
          sx={{ color: "text.tertiary" }}
        />
      </ButtonBase>

      {/* Pass rate per environment, side by side — this run's in full weight. */}
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={2.25}
        sx={{ minWidth: 0, overflow: "hidden" }}
      >
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >
          Pass rate
        </Typography>
        {members.map((m) => (
          <Tooltip
            key={m.runId}
            arrow
            title={
              m.identity
                ? `${m.envName} · ${m.identity}`
                : `${m.envName} · this run was removed`
            }
          >
            <Stack
              direction="row"
              alignItems="baseline"
              spacing={0.625}
              {...clickable(() => open(m), {
                role: "link",
                disabled: m.current || m.missing,
              })}
              sx={{
                ...focusRing,
                minWidth: 0,
                cursor: m.current || m.missing ? "default" : "pointer",
                "&:hover .grp-env":
                  !m.current && !m.missing
                    ? { color: "text.primary", textDecoration: "underline" }
                    : {},
              }}
            >
              <Typography
                noWrap
                className="grp-env"
                sx={{
                  typography: "s3",
                  maxWidth: 180,
                  color: m.current ? "text.primary" : "text.secondary",
                  fontWeight: m.current ? 600 : 500,
                }}
              >
                {m.envName}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                  fontWeight: m.current ? 600 : 500,
                  color: m.result.endsWith("%")
                    ? "text.primary"
                    : "text.tertiary",
                }}
              >
                {m.result}
              </Typography>
            </Stack>
          </Tooltip>
        ))}
      </Stack>

      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { mt: 0.5, py: 0.5, minWidth: 320, borderRadius: "8px" },
          },
        }}
      >
        <Box sx={{ px: 2, pt: 1, pb: 0.75 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.tertiary",
              textTransform: "none",
              letterSpacing: 0.4,
            }}
          >
            Run group
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            One agent version, one run per environment, started together.
          </Typography>
        </Box>
        {members.map((m) => (
          <MenuItem
            key={m.runId}
            disabled={m.missing}
            selected={m.current}
            onClick={() => open(m)}
            sx={{ gap: 1, py: 0.875 }}
          >
            <ListItemText
              primary={m.envName}
              secondary={[m.identity, m.result].filter(Boolean).join(" · ")}
              primaryTypographyProps={{
                typography: "s2",
                fontWeight: m.current ? 600 : 500,
                noWrap: true,
              }}
              secondaryTypographyProps={{
                typography: "s3",
                color: "text.tertiary",
                noWrap: true,
              }}
            />
            {m.current && (
              <Iconify
                icon="solar:check-linear"
                width={14}
                sx={{ color: "text.primary", flexShrink: 0 }}
              />
            )}
          </MenuItem>
        ))}
      </Popover>
    </Stack>
  );
}

RunGroupBar.propTypes = {
  envId: PropTypes.string,
  runId: PropTypes.string,
  /* This run is streaming now — its own rate isn't one yet. */
  live: PropTypes.bool,
};
