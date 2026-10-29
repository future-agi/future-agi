import PropTypes from "prop-types";
import { useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Dialog,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import CopyCommandButton from "../../components/CopyCommandButton";
import {
  TAG_CATEGORIES,
  TAG_COLORS,
  SUGGESTED_TAGS,
  tagsOf,
  groupsOf,
  editTagPatch,
  deleteTagPatch,
  deleteGroupPatch,
  scenarioCli,
} from "../../_mock/scenarioOrg";

/**
 * Tags and groups in one place (roadmap scenarios.md "Scenario table and
 * organization"): a tag's name, colour, category and description — renaming
 * keeps its identity in every view, past run selections stay frozen; and
 * each group with its size. Deleting a group never deletes its scenarios.
 */
export function TagsGroupsManager({
  open,
  onClose,
  envState,
  patch,
  onShowGroup,
  readOnly = false,
}) {
  const tags = tagsOf(envState);
  const groups = groupsOf(envState);
  const count = (id) =>
    (envState.scenarios || []).filter((r) => (r.tagIds || []).includes(id))
      .length;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, p: 2 } }}
    >
      <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600, flex: 1 }}>
          Tags and groups
        </Typography>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify
            icon="solar:close-circle-linear"
            width={20}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        Tags — describe and filter
      </Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          mb: 2,
        }}
      >
        {tags.length === 0 && (
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              px: 1.5,
              py: 1.25,
            }}
          >
            {`No tags yet. Add one from a scenario or a selection — e.g. ${SUGGESTED_TAGS.slice(0, 3).join(", ")}.`}
          </Typography>
        )}
        {tags.map((t) => (
          <Stack
            key={t.id}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.75,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Select
              size="small"
              value={t.color}
              disabled={readOnly}
              onChange={(e) =>
                patch(editTagPatch(envState, t.id, { color: e.target.value }))
              }
              sx={{ width: 60 }}
              renderValue={(c) => (
                <Box
                  sx={{
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    bgcolor: c,
                  }}
                />
              )}
            >
              {TAG_COLORS.map((c) => (
                <MenuItem key={c} value={c}>
                  <Box
                    sx={{
                      width: 12,
                      height: 12,
                      borderRadius: "50%",
                      bgcolor: c,
                    }}
                  />
                </MenuItem>
              ))}
            </Select>
            <TextField
              size="small"
              defaultValue={t.name}
              disabled={readOnly}
              onBlur={(e) =>
                e.target.value.trim() &&
                e.target.value !== t.name &&
                patch(
                  editTagPatch(envState, t.id, { name: e.target.value.trim() }),
                )
              }
              sx={{ width: 170 }}
            />
            <Select
              size="small"
              value={t.category}
              disabled={readOnly}
              onChange={(e) =>
                patch(
                  editTagPatch(envState, t.id, { category: e.target.value }),
                )
              }
              sx={{ width: 130, typography: "s2" }}
            >
              {TAG_CATEGORIES.map((c) => (
                <MenuItem key={c.id} value={c.id} sx={{ typography: "s2" }}>
                  {c.label}
                </MenuItem>
              ))}
            </Select>
            <TextField
              size="small"
              placeholder="Description"
              defaultValue={t.description}
              disabled={readOnly}
              onBlur={(e) =>
                e.target.value !== t.description &&
                patch(
                  editTagPatch(envState, t.id, { description: e.target.value }),
                )
              }
              sx={{ flex: 1 }}
            />
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                width: 28,
                textAlign: "right",
              }}
            >
              {count(t.id)}
            </Typography>
            {!readOnly && (
              <IconButton
                size="small"
                onClick={() => patch(deleteTagPatch(envState, t.id))}
                aria-label={`Delete ${t.name}`}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            )}
          </Stack>
        ))}
      </Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        Groups — select scenarios for a run
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {groups.length === 0 && (
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              px: 1.5,
              py: 1.25,
            }}
          >
            No groups yet. Select scenarios and choose Add to group — e.g.
            Refund release gate, Smoke.
          </Typography>
        )}
        {groups.map((g) => (
          <Stack
            key={g.id}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.75,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Iconify
              icon="solar:folder-2-linear"
              width={16}
              sx={{ color: "text.secondary" }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
              {g.name}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary" }}
            >{`${g.ids.length} scenario${g.ids.length === 1 ? "" : "s"}`}</Typography>
            <Button
              size="small"
              onClick={() => {
                onShowGroup?.(g.name);
                onClose();
              }}
              sx={{ color: "text.secondary" }}
            >
              Show
            </Button>
            {!readOnly && (
              <IconButton
                size="small"
                onClick={() => patch(deleteGroupPatch(envState, g.id))}
                aria-label={`Delete ${g.name}`}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            )}
          </Stack>
        ))}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}>
        Deleting a group keeps its scenarios. Renaming a tag updates it
        everywhere; runs keep the scenarios they ran.
      </Typography>
    </Dialog>
  );
}

TagsGroupsManager.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  envState: PropTypes.object,
  patch: PropTypes.func,
  onShowGroup: PropTypes.func,
  readOnly: PropTypes.bool,
};

const BULK = {
  tag: {
    title: (n) => `Add a tag to ${n} scenarios`,
    verb: "Add tag",
    input: "tag",
    line: "Tags describe and filter. No version is made; past results don't change.",
  },
  group: {
    title: (n) => `Add ${n} scenarios to a group`,
    verb: "Add to group",
    input: "group",
    line: "The same scenarios, not copies — they stay in any other group too.",
  },
  duplicate: {
    title: (n) => `Duplicate ${n} scenarios?`,
    verb: "Duplicate",
    line: "Each copy is a new draft with a link to its original; the originals keep their history.",
  },
  validate: {
    title: (n) => `Validate ${n} scenarios?`,
    verb: "Validate",
    line: "Runs readiness, a valid solution and task-specific negative controls on each. A failure keeps it blocked.",
  },
  archive: {
    title: (n) => `Archive ${n} scenarios?`,
    verb: "Archive",
    line: "Out of new default runs; still in history and every past run.",
  },
  promote: {
    title: (n) => `Promote ${n} scenarios to regression?`,
    verb: "Promote",
    line: "Freezes each reviewed case and its checks, and makes it must-pass.",
  },
  export: {
    title: (n) => `Export ${n} scenarios?`,
    verb: "Export",
    line: "Definitions, tags and citations — never evaluator-only references.",
  },
};

/**
 * One bulk operation on an exact selection (roadmap scenarios.md: "the
 * confirmation shows the exact count and exclusions").
 */
export function BulkOrgDialog({
  kind,
  ids,
  excluded = 0,
  envState,
  env,
  onClose,
  onConfirm,
}) {
  const [value, setValue] = useState("");
  if (!kind) return null;
  const b = BULK[kind];
  const n = ids.length;
  const options =
    b.input === "tag"
      ? [
          ...new Set([
            ...tagsOf(envState).map((t) => t.name),
            ...SUGGESTED_TAGS,
          ]),
        ]
      : groupsOf(envState).map((g) => g.name);
  const cli =
    kind === "tag"
      ? scenarioCli.tag(ids, value)
      : kind === "group"
        ? scenarioCli.group(ids, value)
        : kind === "validate"
          ? scenarioCli.validate(ids)
          : kind === "promote"
            ? scenarioCli.promote(ids)
            : kind === "export"
              ? scenarioCli.export(env)
              : scenarioCli.duplicate(ids[0]);
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      title={b.title(n)}
      content={
        <Stack spacing={1.25}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`${b.line}${excluded ? ` ${excluded} selected can't take this and are left out.` : ""}`}
          </Typography>
          {b.input && (
            <Autocomplete
              freeSolo
              size="small"
              options={options}
              inputValue={value}
              onInputChange={(_, v) => setValue(v)}
              renderInput={(p) => (
                <TextField
                  {...p}
                  autoFocus
                  placeholder={
                    b.input === "tag" ? "risk=duplicate" : "Refund release gate"
                  }
                />
              )}
            />
          )}
          <Box>
            <CopyCommandButton command={cli} />
          </Box>
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color={kind === "archive" ? "error" : "primary"}
          disabled={!!b.input && !value.trim()}
          onClick={() => onConfirm(kind, value.trim())}
        >
          {`${b.verb} · ${n}`}
        </Button>
      }
    />
  );
}

BulkOrgDialog.propTypes = {
  kind: PropTypes.string,
  ids: PropTypes.array,
  excluded: PropTypes.number,
  envState: PropTypes.object,
  env: PropTypes.object,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};
