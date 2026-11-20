import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { VERSIONS_COPY } from "../workspace.constants";

const { diff: COPY } = VERSIONS_COPY;

const list = (value) => (Array.isArray(value) ? value : []);
// A changed actor comes as {id, name}; everything else as its name.
const label = (one) =>
  one && typeof one === "object" ? String(one.name || one.id) : String(one);
// A field the copy doesn't name yet still reads as words: "max_turns" → "Max turns".
const fieldWord = (field) =>
  COPY.scenarioFields[field] ||
  String(field)
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());

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
  const added = list(change?.added).map(label);
  const removed = list(change?.removed).map(label);
  const changed = list(change?.changed).map(label);
  if (!added.length && !removed.length && !changed.length && !children)
    return null;
  return (
    <Stack spacing={0.5} data-testid={`diff-group-${title}`}>
      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
        {title}
      </Typography>
      <NameLine label={COPY.added} names={added} tone="status.success.fg" />
      <NameLine label={COPY.removed} names={removed} tone="status.danger.fg" />
      <NameLine label={COPY.changed} names={changed} tone="status.warning.fg" />
      {children}
    </Stack>
  );
}
// Rules and use cases are sentences: one per line, the old wording struck
// through and the new marked added, as the design shows a reworded rule.
function WordingGroup({ title, change }) {
  const removed = list(change?.removed).map(label);
  const added = list(change?.added).map(label);
  if (!removed.length && !added.length) return null;
  const line = { display: "block", typography: "s2" };
  return (
    <Stack spacing={0.5} data-testid={`diff-group-${title}`}>
      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
        {title}
      </Typography>
      {removed.map((text) => (
        <Typography
          key={`removed-${text}`}
          component="del"
          sx={{
            ...line,
            color: "text.disabled",
            textDecoration: "line-through",
          }}
        >
          {text}
        </Typography>
      ))}
      {added.map((text) => (
        <Typography
          key={`added-${text}`}
          component="ins"
          sx={{ ...line, color: "status.success.fg", textDecoration: "none" }}
        >
          {text}
        </Typography>
      ))}
    </Stack>
  );
}
WordingGroup.propTypes = {
  title: PropTypes.string.isRequired,
  change: PropTypes.object,
};

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
      <WordingGroup title={COPY.rules} change={contract.rules} />
      <WordingGroup title={COPY.useCases} change={contract.use_cases} />
      {fields.length > 0 && (
        <ChangeGroup title={COPY.fields} change={{ changed: fields }} />
      )}
      {contract.modality && (
        <Stack spacing={0.5}>
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {COPY.modality}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {String(contract.modality.from ?? "—")} →{" "}
            {String(contract.modality.to ?? "—")}
          </Typography>
        </Stack>
      )}
      <ChangeGroup title={COPY.subGoals} change={diff?.sub_goals} />
      {diff?.actors === null ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {COPY.actorsUnknown}
        </Typography>
      ) : (
        <ChangeGroup title={COPY.actors} change={diff?.actors} />
      )}
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

// Changed scenarios gathered by the fields that changed, in first-seen order,
// so "Passes when" across three scenarios is said once.
const groupByFields = (changed) => {
  const groups = new Map();
  changed.forEach(([key, fields]) => {
    const words = list(fields).map(fieldWord);
    const id = words.join("\u0000");
    if (!groups.has(id)) groups.set(id, { words, keys: [] });
    groups.get(id).keys.push(key);
  });
  return [...groups.values()];
};

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
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
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
              {one?.reason ? ` · ${one.reason}` : ""}
            </Typography>
          ))}
        </Stack>
      )}
      {changed.length > 0 && (
        <Stack spacing={1} data-testid={`diff-group-${COPY.scenariosChanged}`}>
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {COPY.scenariosChanged}
          </Typography>
          {groupByFields(changed).map(({ words, keys }) => (
            <Box key={words.join()}>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {COPY.changedGroup(words, keys.length)}
              </Typography>
              {keys.map((key) => (
                <Typography
                  key={key}
                  sx={{
                    typography: "s2",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    mt: 0.25,
                  }}
                >
                  {key}
                </Typography>
              ))}
            </Box>
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
