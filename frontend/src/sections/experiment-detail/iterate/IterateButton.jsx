// R2 (L2) · the `Iterate` button (R2-SPEC §4.2): experiment header (left
// of `Rerun Experiment ▾`), compare header (left of `Copy link`) and the
// leaderboard selection bar. It writes the one-shot `iterate=` URL; the
// dock on the experiment / compare page opens on it (one dock, many doors).
import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { VOCAB } from "src/sections/common/vocab";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRunConfigs } from "src/api/develop/r2";
import {
  iterateHref,
  readR2Params,
} from "src/sections/common/Analysis/analysisLinks";
import { canIterate, iterableFocusOf } from "./iterateModel";

// v2.1 g4 (change:prompt:messages): what the dock really changes — a
// prompt run's model, settings and tools; an agent's node prompt only
// through a finding's fixes (free-text prompt edits are not offered).
export const ITERATE_TIP =
  "Change the model, settings or tools (an agent's node prompt through a finding's fixes), rerun some rows as a new run, and compare it with this one.";
export const VIEWER_TIP = "Editors can start runs";

/** Pages that mount the dock (experiment and compare). */
const hasDock = (pathname) =>
  /\/develop\/experiment\/[^/]+/.test(pathname) ||
  /\/develop\/[^/]+\/compare\//.test(pathname);

/**
 * The link an Iterate door opens: the same page when it mounts the dock,
 * else the run's experiment page (leaderboard). Pure; exported for tests.
 */
export function iterateDoorHref({
  pathname,
  search,
  runId,
  experimentId,
  scope = "all",
  rows,
  change,
  cause,
}) {
  const opts = { runId, scope, rows, change, cause };
  if (hasDock(pathname)) return `${pathname}${iterateHref(search, opts)}`;
  if (experimentId)
    return iterateHref(
      `/dashboard/develop/experiment/${experimentId}/data`,
      opts,
    );
  return null;
}

const IterateButton = ({
  datasetId,
  experimentId,
  runIds,
  runId,
  baselineId,
  size = "medium",
}) => {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  // dataset integration FE-0: the runs are read even for a named run, so a run Test a
  // fix does not apply to (code, harness, voice, upload, endpoint) gets no
  // button at all (owner Q12: absent, not disabled).
  const configs = useRunConfigs(datasetId, { enabled: Boolean(datasetId) });
  const runs = configs.data?.results ?? [];
  const params = readR2Params(search);
  const target =
    runId ??
    iterableFocusOf({
      runIds,
      runs,
      baselineId,
      cand: params.cand,
      urlBaseline: new URLSearchParams(search).get("baseline"),
    });
  const targetRun = runs.find((r) => r.run_id === target) ?? null;
  const absent = targetRun
    ? !canIterate(targetRun)
    : Boolean(runIds?.length) && configs.isSuccess && !target;
  const exp =
    experimentId ??
    runs.find((r) => r.run_id === target)?.experiment_id ??
    null;
  const href = target
    ? iterateDoorHref({ pathname, search, runId: target, experimentId: exp })
    : null;
  const reason = !canEdit ? VIEWER_TIP : !href ? "Pick a run first" : null;
  // R4 implementation (8e): the visible label is `Test a fix`; ⌘K still finds it
  // as `Iterate`.
  useRegisterCommands(
    "test-a-fix",
    [
      {
        id: "testAFix.open",
        label: VOCAB.TEST_A_FIX,
        keywords: ["iterate", "fix", "rerun", "change the prompt"],
        icon: "mdi:source-branch",
        run: () => href && navigate(href),
        when: () => Boolean(href) && !reason && !absent,
      },
    ],
    [href, reason, absent],
  );
  if (absent) return null;
  return (
    <CustomTooltip show title={reason ?? ITERATE_TIP} size="small" arrow>
      <span>
        <Button
          variant="outlined"
          color="primary"
          size={size}
          disabled={Boolean(reason)}
          data-testid="iterate-button"
          startIcon={<Iconify icon="mdi:source-branch" width={18} />}
          onClick={() => navigate(href)}
        >
          {VOCAB.TEST_A_FIX}
        </Button>
      </span>
    </CustomTooltip>
  );
};

IterateButton.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  runId: PropTypes.string,
  baselineId: PropTypes.string,
  trial: PropTypes.number,
  rowId: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
};

export default IterateButton;
