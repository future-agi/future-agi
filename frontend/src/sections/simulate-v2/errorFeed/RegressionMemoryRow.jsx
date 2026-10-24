import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useSimStore } from "../store";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import {
  declineMemoryPatch,
  regressionMemoryOf,
  rememberPatch,
} from "../_mock/releaseFix";
import { useIssueFix } from "./issueFix";

/**
 * Regression memory on the Error Feed issue, once its fix is released (RSI
 * PRD: every fix leaves a regression scenario behind; a fixed issue's
 * scenarios become must-pass once the user approves). The same question the
 * improvement page asks — here too, because a release is often detected
 * while nobody is on that page. Not now stays reversible.
 */
export default function RegressionMemoryRow({ issue }) {
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { status } = useIssueFix(issue);
  const { state, commit } = useSimStore();
  if (!improving || !status?.optId) return null;
  const env = state.myEnvironments.find((e) => e.id === status.envId);
  const es = state.byEnv[status.envId];
  const memory = regressionMemoryOf(es, status.optId);
  if (!memory || memory.status === "none") return null;
  /* A reopened fix didn't hold — don't offer its scenarios as proof it can't come back. */
  if (status.key === "reopened" && memory.status !== "kept") return null;
  const n = memory.offer.length;
  const envName = env?.name || "the environment";
  const keep = () =>
    commit({
      type: "patchEnvState",
      envId: status.envId,
      patch: rememberPatch(es, {
        optId: status.optId,
        ids: memory.offer.map((x) => x.id),
        version: memory.version,
      }),
    });
  const notNow = () =>
    commit({
      type: "patchEnvState",
      envId: status.envId,
      patch: declineMemoryPatch(es, status.optId),
    });
  const openScenarios = () =>
    navigate(
      `${paths.dashboard.simulate.environmentDetail(status.envId)}/scenarios`,
    );
  const plural = (k, one, many) => `${k} ${k === 1 ? one : many}`;

  if (memory.status === "kept") {
    return (
      <Row icon="solar:shield-check-linear">
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          {`${plural(memory.kept.length, "scenario", "scenarios")} from this issue ${memory.kept.length === 1 ? "is" : "are"} must-pass in ${envName} — a change that fails ${memory.kept.length === 1 ? "it" : "one"} again is rejected.`}
        </Typography>
        <Button
          size="small"
          onClick={openScenarios}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        >
          View scenarios
        </Button>
      </Row>
    );
  }

  if (memory.status === "declined") {
    return (
      <Row icon="solar:shield-linear">
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {`Not kept as must-pass — ${plural(n, "scenario", "scenarios")} from this fix could still guard against it coming back.`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={keep}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Keep as must-pass
        </Button>
      </Row>
    );
  }

  return (
    <Row icon="solar:shield-check-linear">
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`Keep ${plural(n, "scenario", "scenarios")} from this fix as must-pass in ${envName}?`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`${n === 1 ? "It passed" : "They passed"} every repeat on ${memory.version}. A later change that fails one again is rejected, so this can't quietly come back.${memory.targets.length ? ` ${plural(memory.targets.length, "other still fails", "others still fail")} sometimes.` : ""}`}
        </Typography>
      </Box>
      <Button
        size="small"
        onClick={notNow}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      >
        Not now
      </Button>
      <Button
        size="small"
        variant="contained"
        color="primary"
        onClick={keep}
        sx={{ flexShrink: 0 }}
      >
        Keep as must-pass
      </Button>
    </Row>
  );
}
RegressionMemoryRow.propTypes = { issue: PropTypes.object };

function Row({ icon, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      {children}
    </Stack>
  );
}
Row.propTypes = { icon: PropTypes.string, children: PropTypes.node };
