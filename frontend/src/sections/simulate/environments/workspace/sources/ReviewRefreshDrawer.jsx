import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { kbChangesOf } from "src/api/simulate-environments/_fixtures/sources";
import {
  useEnvironmentSources,
  useRefreshSource,
} from "src/api/simulate-environments/sources";
import SideDrawer from "../../components/SideDrawer";
import UiOnlyFlag from "../../components/UiOnlyFlag";

const KIND_LABEL = {
  "rule-added": "rule added",
  "rule-changed": "rule changed",
  "row-added": "rows added",
  "row-changed": "rows changed",
  "row-removed": "rows removed",
};
const GROUPS = [
  ["rule", "Rules"],
  ["row", "Rows"],
];
// Stands in for validating the accepted changes before saving.
const VALIDATE_MS = 500;

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// "Affects N scenarios", the names on hover.
function ScenarioCount({ names }) {
  return (
    <CustomTooltip show arrow title={names.join(" · ")}>
      <Typography
        component="span"
        sx={{
          typography: "s3",
          color: "text.tertiary",
          textDecoration: "underline dotted",
          width: "fit-content",
        }}
      >
        {`Affects ${plural(names.length, "scenario")}`}
      </Typography>
    </CustomTooltip>
  );
}

ScenarioCount.propTypes = {
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
};

/**
 * Review refresh: a Knowledge base file changed since its copy was added.
 * Each change is accepted unless rejected; Save as vN takes the accepted ones
 * as the file's next version and clears the changed line. Keep my copy
 * leaves it as it is, still waiting to be reviewed.
 */
export default function ReviewRefreshDrawer({ envId, sourceId, onClose }) {
  const { data: summary } = useEnvironmentSources(envId);
  const refresh = useRefreshSource(envId);
  const [rejected, setRejected] = useState(() => new Set());
  const [validating, setValidating] = useState(false);

  const source = summary?.sources.find((one) => one.source_id === sourceId);
  if (!source) return null;
  // shortcut: the change list is fixture-only; read it from the API once it reports Knowledge base changes.
  const changes = kbChangesOf(source);
  const accepted = changes.filter((change) => !rejected.has(change.id));
  const touched = [...new Set(accepted.flatMap((change) => change.scenarios))];
  const next = `v${source.version + 1}`;

  const decide = (id, value) => {
    if (!value) return;
    setRejected((current) => {
      const updated = new Set(current);
      if (value === "reject") updated.add(id);
      else updated.delete(id);
      return updated;
    });
  };

  const keepCopy = () => {
    enqueueSnackbar(
      `Kept your copy of ${source.name}. The change stays available to review.`,
      { variant: "info" },
    );
    onClose();
  };

  const save = () => {
    setValidating(true);
    setTimeout(() => {
      refresh(sourceId, accepted);
      enqueueSnackbar(`Saved as ${next} with the current ${source.name}.`, {
        variant: "success",
      });
      onClose();
    }, VALIDATE_MS);
  };

  return (
    // Not closable while saving, so the save can't land after a close.
    <SideDrawer open onClose={validating ? () => {} : onClose} width={560}>
      <Stack sx={{ height: "100%" }}>
        <Box
          sx={{
            px: 2.5,
            pt: 2,
            pb: 1.25,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "m1", minWidth: 0 }}>
              {`Review refresh · ${source.name}`}
            </Typography>
            <UiOnlyFlag feature="environmentSources" />
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Changed in the Knowledge base since you added it · your copy vs the current file · ${plural(changes.length, "change")}, each accepted unless you reject it, then Save as ${next}`}
          </Typography>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {GROUPS.map(([group, label]) => {
            const list = changes.filter((change) =>
              change.kind.startsWith(group),
            );
            if (!list.length) return null;
            return (
              <Box key={group}>
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    px: 2.5,
                    pt: 1.5,
                    pb: 0.25,
                  }}
                >
                  {`${label} · ${list.length}`}
                </Typography>
                {list.map((change) => (
                  <Stack
                    key={change.id}
                    direction="row"
                    spacing={1.5}
                    sx={{
                      px: 2.5,
                      py: 1,
                      "& + &": {
                        borderTop: "1px solid",
                        borderColor: "divider",
                      },
                    }}
                  >
                    <Stack spacing={0.25} sx={{ flex: 1, minWidth: 0 }}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {change.label}
                        <Box
                          component="span"
                          sx={{ fontWeight: 400, color: "text.tertiary" }}
                        >
                          {` · ${[KIND_LABEL[change.kind], change.ref].filter(Boolean).join(" · ")}`}
                        </Box>
                      </Typography>
                      {change.from && (
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            textDecoration: "line-through",
                          }}
                        >
                          {change.from}
                        </Typography>
                      )}
                      {change.to && (
                        <Typography sx={{ typography: "s3" }}>
                          {change.to}
                        </Typography>
                      )}
                      {change.detail && (
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {change.detail}
                        </Typography>
                      )}
                      {change.scenarios.length > 0 && (
                        <ScenarioCount names={change.scenarios} />
                      )}
                    </Stack>
                    <ToggleButtonGroup
                      size="small"
                      exclusive
                      value={rejected.has(change.id) ? "reject" : "accept"}
                      onChange={(_, value) => decide(change.id, value)}
                      aria-label={`${change.label}: accept or reject`}
                      sx={{ alignSelf: "flex-start", flexShrink: 0 }}
                    >
                      <ToggleButton value="accept">Accept</ToggleButton>
                      <ToggleButton value="reject">Reject</ToggleButton>
                    </ToggleButtonGroup>
                  </Stack>
                ))}
              </Box>
            );
          })}
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5} flex={1}>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`${accepted.length} of ${changes.length} accepted`}
            </Typography>
            {touched.length > 0 && <ScenarioCount names={touched} />}
          </Stack>
          <Button
            size="small"
            variant="outlined"
            disabled={validating}
            onClick={keepCopy}
          >
            Keep my copy
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={validating || !accepted.length}
            onClick={save}
          >
            {validating ? "Validating…" : `Save as ${next}`}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

ReviewRefreshDrawer.propTypes = {
  envId: PropTypes.string,
  sourceId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};
