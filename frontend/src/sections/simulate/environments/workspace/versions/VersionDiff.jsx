import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { VERSIONS_COPY } from "../workspace.constants";

const { diff: COPY } = VERSIONS_COPY;

const list = (value) => (Array.isArray(value) ? value : []);

// One labelled group of names, e.g. "Added: lookup_order, refund".
function NameLine({ label, names, tone }) {
  if (!names.length) return null;
  return (
    <Typography sx={{ typography: "s2", color: "text.secondary" }}>
      <Box
        component="span"
        sx={{ color: tone, fontWeight: "fontWeightSemiBold" }}
      >
        {label}:
      </Box>{" "}
      <Box
        component="span"
        sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
      >
        {names.join(", ")}
      </Box>
    </Typography>
  );
}
NameLine.propTypes = {
  label: PropTypes.string.isRequired,
  names: PropTypes.arrayOf(PropTypes.string).isRequired,
  tone: PropTypes.string,
};

// A section of added / removed / changed names; renders nothing when all are empty.
function ChangeGroup({ title, change, children }) {
  const added = list(change?.added).map(String);
  const removed = list(change?.removed).map(String);
  const changed = list(change?.changed).map(String);
  if (!added.length && !removed.length && !changed.length && !children)
    return null;
  return (
    <Stack spacing={0.5} data-testid={`diff-group-${title}`}>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
        {title}
      </Typography>
      <NameLine label={COPY.added} names={added} tone="success.main" />
      <NameLine label={COPY.removed} names={removed} tone="error.main" />
      <NameLine label={COPY.changed} names={changed} tone="warning.main" />
      {children}
    </Stack>
  );
}
ChangeGroup.propTypes = {
  title: PropTypes.string.isRequired,
  change: PropTypes.object,
  children: PropTypes.node,
};

function EnvironmentDiff({ diff }) {
  const contract = diff?.contract || {};
  const fields = list(contract.fields).map(String);
  return (
    <Stack spacing={1.5}>
      <ChangeGroup title={COPY.tools} change={contract.tools} />
      <ChangeGroup title={COPY.rules} change={contract.rules} />
      <ChangeGroup title={COPY.useCases} change={contract.use_cases} />
      {fields.length > 0 && (
        <ChangeGroup title={COPY.fields} change={{ changed: fields }} />
      )}
      {contract.modality && (
        <Stack spacing={0.5}>
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
            {COPY.modality}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {String(contract.modality.from ?? "—")} →{" "}
            {String(contract.modality.to ?? "—")}
          </Typography>
        </Stack>
      )}
      <ChangeGroup title={COPY.subGoals} change={diff?.sub_goals} />
      {diff?.files === null ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {COPY.filesUnknown}
        </Typography>
      ) : (
        <ChangeGroup title={COPY.files} change={diff?.files} />
      )}
    </Stack>
  );
}
EnvironmentDiff.propTypes = { diff: PropTypes.object };

function SuiteDiff({ diff }) {
  const added = list(diff?.added).map(String);
  const removed = list(diff?.removed);
  const changed = Object.entries(diff?.changed || {});
  return (
    <Stack spacing={1.5}>
      {added.length > 0 && (
        <ChangeGroup title={COPY.scenariosAdded} change={{ added }} />
      )}
      {removed.length > 0 && (
        <Stack
          spacing={0.5}
          data-testid={`diff-group-${COPY.scenariosRemoved}`}
        >
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
            {COPY.scenariosRemoved}
          </Typography>
          {removed.map((one) => (
            <Typography
              key={one?.key ?? String(one)}
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              <Box
                component="span"
                sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
              >
                {one?.key ?? String(one)}
              </Box>
              {one?.reason ? ` — ${one.reason}` : ""}
            </Typography>
          ))}
        </Stack>
      )}
      {changed.length > 0 && (
        <Stack
          spacing={0.5}
          data-testid={`diff-group-${COPY.scenariosChanged}`}
        >
          <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
            {COPY.scenariosChanged}
          </Typography>
          {changed.map(([key, fields]) => (
            <Typography
              key={key}
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              <Box
                component="span"
                sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}
              >
                {key}
              </Box>
              {`: ${list(fields).join(", ")}`}
            </Typography>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
SuiteDiff.propTypes = { diff: PropTypes.object };

function AgentDiff({ diff }) {
  return (
    <Stack spacing={1.5}>
      {diff?.source && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {COPY.source}
        </Typography>
      )}
      {diff?.prompt && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {COPY.prompt}
        </Typography>
      )}
      <ChangeGroup title={COPY.variables} change={diff?.variables} />
    </Stack>
  );
}
AgentDiff.propTypes = { diff: PropTypes.object };

export default function VersionDiff({ piece, diff }) {
  if (piece === "suite") return <SuiteDiff diff={diff} />;
  if (piece === "agent") return <AgentDiff diff={diff} />;
  return <EnvironmentDiff diff={diff} />;
}
VersionDiff.propTypes = {
  piece: PropTypes.oneOf(["agent", "environment", "suite"]).isRequired,
  diff: PropTypes.object,
};
