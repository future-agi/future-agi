import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useEnvState } from "../../store";
import { refreshPlanOf, regroundPatch } from "../../_mock/orgData";
import {
  keepCaseOnSourcePatch,
  sourceChangeOf,
} from "../../_mock/sourceChange";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";

/**
 * A case grounded in a file that has a newer version waiting (roadmap
 * builder.md Week 2, "source-stale cases stay visible with repair actions"):
 * what changed in the file, then Re-ground — the environment moves to the
 * new version and the cases it touches are re-checked — or keep this case
 * on the version it was made from.
 */
export default function SourceChangedNotice({ row, env, locked }) {
  const { envState, patch } = useEnvState(env.id);
  const change = sourceChangeOf(row, envState);
  const kept =
    !change && row.sourcePinned
      ? (envState.sources || []).find((s) => s.id === row.sourcePinned.sourceId)
      : null;
  if (kept?.pendingRefresh)
    return (
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          px: 2.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {`Kept on ${kept.name} v${row.sourcePinned.v}. v${kept.pendingRefresh.v} is waiting; re-ground it from Sources when this case should follow.`}
      </Typography>
    );
  if (!change) return null;
  const { source, pending } = change;
  const plan = refreshPlanOf(env, envState, source, { name: pending.name });
  const next = nextSnapshotLabel(env, envState);

  return (
    <Box
      sx={{
        px: 2.5,
        py: 1.5,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Iconify
          icon="solar:danger-triangle-linear"
          width={14}
          sx={{ color: "warning.main" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`Source changed · ${source.name} v${pending.v}`}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}>
        {`This case was grounded in v${source.version}. The new version was kept aside, so runs still use v${source.version}.`}
      </Typography>
      {(pending.changed || []).map((c) => (
        <Box
          key={c.id}
          sx={{
            mt: 0.75,
            pl: 1.25,
            borderLeft: "2px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              textDecoration: "line-through",
            }}
          >{`p.${c.page} “${c.from}”`}</Typography>
          <Typography
            sx={{ typography: "s3" }}
          >{`p.${c.page} “${c.to}”`}</Typography>
        </Box>
      ))}
      {!locked && (
        <Stack direction="row" spacing={1} sx={{ mt: 1.25 }}>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              patch(regroundPatch(env, envState, source, plan));
              enqueueSnackbar(
                `Re-grounded on ${source.name} v${pending.v} as environment ${next}. ${plan.affected.length} case${plan.affected.length === 1 ? "" : "s"} to re-check.`,
                { variant: "success" },
              );
            }}
          >
            {`Re-ground · saves environment ${next}`}
          </Button>
          <Button
            size="small"
            color="inherit"
            onClick={() =>
              patch(keepCaseOnSourcePatch(envState, row.id, source))
            }
          >
            {`Keep this case on v${source.version}`}
          </Button>
        </Stack>
      )}
      {!locked && plan.affected.length > 1 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {`Re-grounding moves the whole environment: ${plan.affected.length} cases that touch this file are re-checked.`}
        </Typography>
      )}
    </Box>
  );
}

SourceChangedNotice.propTypes = {
  row: PropTypes.object.isRequired,
  env: PropTypes.object.isRequired,
  locked: PropTypes.bool,
};
