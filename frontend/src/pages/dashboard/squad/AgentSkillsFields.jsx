import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { MAX_AGENT_SKILLS, normalizeSkillRefs } from "./useAgentSkills";
const positive = (value) => Number.isSafeInteger(value) && value > 0;
const refsEqual = (a, b) =>
  a.length === b.length &&
  a.every(
    (ref, index) =>
      ref.id === b[index].id && ref.revision === b[index].revision,
  );

export default function AgentSkillsFields({
  value,
  onChange,
  selection,
  disabled = false,
}) {
  const refsState = normalizeSkillRefs(value);
  const refs = refsState.refs;
  const catalog = Array.isArray(selection?.catalog) ? selection.catalog : [];
  const revisions = selection?.revisions || {};
  const selected = new Map(refs.map((ref) => [ref.id, ref]));
  const selectedUnknown = refs.filter(
    (ref) => !catalog.some((skill) => skill.id === ref.id),
  );
  const selectedArchived = refs.filter((ref) =>
    catalog.some((skill) => skill.id === ref.id && skill.status !== "active"),
  );
  const change = (next) => {
    if (disabled || !refsState.validShape || typeof onChange !== "function")
      return;
    const unique = [];
    for (const ref of next)
      if (!unique.some((entry) => entry.id === ref.id))
        unique.push({ id: ref.id, revision: ref.revision });
    if (unique.length <= MAX_AGENT_SKILLS && !refsEqual(unique, refs))
      onChange(unique);
  };
  const remove = (id) => change(refs.filter((ref) => ref.id !== id));
  const errorEntries = Object.entries(selection?.errors || {});
  return (
    <Card component="section" variant="outlined" aria-label="Agent skills">
      <CardContent>
        <Stack spacing={1.5}>
          <Box>
            <Typography variant="h6">Agent skills</Typography>
            <Typography variant="body2" color="text.secondary">
              Select immutable workspace instruction revisions. Skills do not
              grant tools, credentials, or permissions.
            </Typography>
          </Box>
          {selection?.loading ? (
            <CircularProgress size={22} aria-label="Loading skills" />
          ) : null}
          {!selection?.allowed ? (
            <Alert severity="info">
              Skill selection is unavailable for this agent.
            </Alert>
          ) : null}
          {errorEntries.map(([key, message]) => (
            <Alert severity="warning" key={key}>
              {message}
            </Alert>
          ))}
          {!refsState.validShape ? (
            <Alert severity="warning">
              The saved skill selection is malformed. Remove it before saving
              this agent.
            </Alert>
          ) : null}
          {selection?.currentValid === false && refs.length ? (
            <Alert severity="warning">
              One or more saved skill revisions are unavailable or archived.
              Remove them before saving.
            </Alert>
          ) : null}
          {catalog
            .filter((skill) => skill.status === "active")
            .map((skill) => {
              const ref = selected.get(skill.id);
              const list = Array.isArray(revisions[skill.id])
                ? revisions[skill.id]
                : [];
              // Selection captures the validated catalog's latest revision. The
              // hook loads its history lazily and refuses saving until it verifies
              // this exact revision against the same catalog snapshot.
              const latest =
                selection?.canSelect && !selection?.errors?.[skill.id]
                  ? skill
                  : null;
              return (
                <Card variant="outlined" key={skill.id}>
                  <CardContent>
                    <Stack spacing={1}>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={Boolean(ref)}
                            disabled={
                              disabled ||
                              (!ref &&
                                (!latest || refs.length >= MAX_AGENT_SKILLS))
                            }
                            onChange={(event) => {
                              if (event.target.checked && latest)
                                change([
                                  ...refs.filter(
                                    (item) => item.id !== skill.id,
                                  ),
                                  { id: skill.id, revision: latest.revision },
                                ]);
                              else if (!event.target.checked) remove(skill.id);
                            }}
                          />
                        }
                        label={
                          <span>
                            {skill.name}{" "}
                            <Typography
                              component="span"
                              variant="caption"
                              color="text.secondary"
                            >
                              ({skill.id})
                            </Typography>
                          </span>
                        }
                      />
                      <Typography variant="body2" color="text.secondary">
                        {skill.description}
                      </Typography>
                      {ref ? (
                        <TextField
                          select
                          SelectProps={{ native: true }}
                          size="small"
                          label={"Revision for " + skill.name}
                          value={String(ref.revision)}
                          disabled={
                            disabled ||
                            !selection?.canSelect ||
                            !list.length ||
                            Boolean(selection?.errors?.[skill.id])
                          }
                          onChange={(event) => {
                            const next = Number(event.target.value);
                            if (
                              positive(next) &&
                              list.some((entry) => entry.revision === next)
                            )
                              change(
                                refs.map((item) =>
                                  item.id === skill.id
                                    ? { id: skill.id, revision: next }
                                    : item,
                                ),
                              );
                          }}
                        >
                          {!list.some(
                            (entry) => entry.revision === ref.revision,
                          ) ? (
                            <option value={ref.revision}>
                              Revision {ref.revision} (unverified)
                            </option>
                          ) : null}
                          {list.map((entry) => (
                            <option value={entry.revision} key={entry.revision}>
                              Revision {entry.revision} · {entry.digest}
                            </option>
                          ))}
                        </TextField>
                      ) : null}
                    </Stack>
                  </CardContent>
                </Card>
              );
            })}
          {selectedArchived.map((ref) => (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              key={"archived-" + ref.id}
            >
              <Chip
                color="warning"
                label={
                  "Archived skill " + ref.id + " · revision " + ref.revision
                }
              />
              <Button
                size="small"
                disabled={disabled}
                onClick={() => remove(ref.id)}
              >
                Remove
              </Button>
            </Stack>
          ))}
          {selectedUnknown.map((ref) => (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              key={"unknown-" + ref.id}
            >
              <Chip
                color="warning"
                label={
                  "Unavailable skill " + ref.id + " · revision " + ref.revision
                }
              />
              <Button
                size="small"
                disabled={disabled}
                onClick={() => remove(ref.id)}
              >
                Remove
              </Button>
            </Stack>
          ))}
          {!catalog.filter((skill) => skill.status === "active").length &&
          !refs.length ? (
            <Typography color="text.secondary">
              No active workspace skills are available.
            </Typography>
          ) : null}
          <Typography variant="caption" color="text.secondary">
            {refs.length} of {MAX_AGENT_SKILLS} skill revisions selected. Older
            selections stay pinned when the skill library changes.
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button
              disabled={disabled || Boolean(selection?.loading)}
              onClick={() => void selection?.refresh?.()}
            >
              Refresh skills
            </Button>
            {refs.length > 0 || !refsState.validShape ? (
              <Button disabled={disabled} onClick={() => onChange?.([])}>
                Clear skill selection
              </Button>
            ) : null}
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}

AgentSkillsFields.propTypes = {
  value: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      revision: PropTypes.number.isRequired,
    }),
  ),
  onChange: PropTypes.func,
  selection: PropTypes.shape({
    catalog: PropTypes.arrayOf(PropTypes.object),
    revisions: PropTypes.object,
    loading: PropTypes.bool,
    errors: PropTypes.object,
    currentValid: PropTypes.bool,
    canSelect: PropTypes.bool,
    refresh: PropTypes.func,
    allowed: PropTypes.bool,
  }),
  disabled: PropTypes.bool,
};
