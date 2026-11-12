// KH integration (H-1 · EV-NA3): "Add a group" beside "Add evaluation" in New
// experiment's Evaluations step. A group's members that are not on the step
// yet are read the way the EvalPicker reads a template (its detail query)
// and handed to the step's own add handler, so the step's one mapping path
// (auto-map, output -> run output, reference keys -> Expected answers)
// applies to them. Groups are managed in Evals (eval-groups API).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  InputAdornment,
  Link,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useDebounce } from "src/hooks/use-debounce";
import { evalDetailQuery } from "src/sections/evals/hooks/useEvalDetail";
import { normalizeEvalPickerEval } from "src/sections/common/EvalPicker/evalPickerValue";
import { EVAL_GROUPS_PATH } from "./evalLinks";
import { pickerAddOf } from "./applyGroupModel";
import {
  addedGroupText,
  groupLine,
  memberPreview,
  membersToAdd,
} from "./evalGroupModel";

const SEARCH_DEBOUNCE_MS = 300; // as the Evals Groups page's search
const PAGE_SIZE = 50; // the menu lists every group a workspace keeps

const groupDetailQuery = (id) => ({
  queryKey: ["eval-handoffs", "group", id],
  queryFn: async () => {
    const { data } = await axios.get(
      `${endpoints.develop.eval.groupEvals}${id}/`,
    );
    return data?.result;
  },
});

// The Evals page's version list (useEvalVersions' key and read).
const versionsQuery = (templateId) => ({
  queryKey: ["evals", "versions", templateId],
  queryFn: async () => {
    const { data } = await axios.get(
      endpoints.develop.eval.getEvalVersions(templateId),
    );
    return data?.result;
  },
});

const currentVersionOf = (versionsData) =>
  (versionsData?.versions ?? []).find((v) => v.is_default) ?? null;

function GroupRow({ group, busy, onChoose }) {
  const { data: detail } = useQuery({
    ...groupDetailQuery(group.id),
    staleTime: 30000,
  });
  return (
    <MenuItem
      onClick={() => onChoose(group)}
      disabled={busy}
      data-testid="add-eval-group-row"
      sx={{ alignItems: "flex-start", py: 1, whiteSpace: "normal" }}
    >
      <Stack spacing={0.25} sx={{ minWidth: 0, flex: 1 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Typography typography="s2" fontWeight="fontWeightMedium">
            {groupLine(group)}
          </Typography>
          {group.is_sample && (
            <Chip
              size="small"
              label="Future AGI built"
              variant="outlined"
              sx={{ height: 18 }}
            />
          )}
        </Stack>
        <Typography typography="s3" color="text.secondary" noWrap>
          {detail ? memberPreview(detail.members) : " "}
        </Typography>
      </Stack>
    </MenuItem>
  );
}

GroupRow.propTypes = {
  group: PropTypes.object.isRequired,
  busy: PropTypes.bool,
  onChoose: PropTypes.func.isRequired,
};

export default function AddEvalGroupButton({
  existing = [],
  onAdd,
  onPick,
  disabled = false,
}) {
  const queryClient = useQueryClient();
  const [anchor, setAnchor] = useState(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const q = useDebounce(search.trim(), SEARCH_DEBOUNCE_MS);
  const { data, isLoading } = useQuery({
    queryKey: ["eval-handoffs", "groups", q],
    queryFn: async () => {
      const { data: res } = await axios.get(endpoints.develop.eval.groupEvals, {
        params: {
          page_number: 0,
          page_size: PAGE_SIZE,
          ...(q ? { name: q } : {}),
        },
      });
      return res?.result;
    },
    enabled: Boolean(anchor),
  });
  const groups = data?.data ?? [];

  const choose = async (group) => {
    setBusy(true);
    try {
      const detail = await queryClient.fetchQuery(groupDetailQuery(group.id));
      const { add, skipped } = membersToAdd(detail, existing);
      // HO D9: a dataset or an experiment applies the group on the server
      // (ApplyEvalGroupDialog); the host takes the choice from here.
      if (onPick) {
        onPick({ group, detail, add, skipped });
        setAnchor(null);
        return;
      }
      const templates = await Promise.all(
        add.map(async (m) => {
          const [tpl, versions] = await Promise.all([
            queryClient.fetchQuery(evalDetailQuery(m.eval_template_id)),
            queryClient.fetchQuery(versionsQuery(m.eval_template_id)),
          ]);
          const current = currentVersionOf(versions);
          return {
            raw: tpl,
            versions: versions?.versions ?? [],
            t: {
              ...tpl,
              template_id: tpl?.id ?? m.eval_template_id,
              version_id: current?.id ?? null,
              version: current ? `v${current.version_number}` : null,
            },
          };
        }),
      );
      // HO D9: each member as the EvalPicker adds it (output type, keys and
      // the version used, pinned), so its card carries the picker's chips.
      templates.forEach(({ raw, versions, t }) =>
        onAdd({
          ...normalizeEvalPickerEval(t),
          ...pickerAddOf(
            { ...raw, id: t.template_id },
            versions.length
              ? versions
              : t.version_id
                ? [{ id: t.version_id, is_default: true }]
                : [],
          ),
        }),
      );
      const name = detail?.eval_group?.name ?? group.name;
      enqueueSnackbar(
        addedGroupText(name, {
          added: templates.length,
          skippedNames: skipped.map(
            ({ entry, member }) => entry.name ?? member.name,
          ),
        }),
        { variant: templates.length ? "success" : "info" },
      );
      setAnchor(null);
    } catch (err) {
      enqueueSnackbar(
        err?.result ?? err?.message ?? "Couldn't read the group",
        {
          variant: "error",
        },
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        disabled={disabled}
        startIcon={<Iconify icon="mdi:folder-multiple-outline" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        data-testid="add-eval-group"
        sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
      >
        Add a group
      </Button>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => !busy && setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 360 } } }}
      >
        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            fullWidth
            autoFocus
            placeholder="Search groups"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="mdi:magnify" width={16} />
                </InputAdornment>
              ),
              endAdornment: busy ? (
                <InputAdornment position="end">
                  <CircularProgress size={14} />
                </InputAdornment>
              ) : null,
            }}
          />
        </Box>
        <Box
          sx={{ maxHeight: 320, overflow: "auto" }}
          data-testid="add-eval-group-menu"
        >
          {isLoading ? (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ px: 2, py: 1.5 }}
            >
              Loading groups…
            </Typography>
          ) : groups.length ? (
            groups.map((g) => (
              <GroupRow key={g.id} group={g} busy={busy} onChoose={choose} />
            ))
          ) : q ? (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ px: 2, py: 1.5 }}
            >
              {`No groups match “${q}”`}
            </Typography>
          ) : (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ px: 2, py: 1.5 }}
            >
              No groups yet ·{" "}
              <Link href={EVAL_GROUPS_PATH} target="_blank" rel="noopener">
                Create one in Evals ↗
              </Link>
            </Typography>
          )}
        </Box>
        <Divider />
        <Box sx={{ px: 2, py: 1 }}>
          <Link
            href={EVAL_GROUPS_PATH}
            target="_blank"
            rel="noopener"
            typography="s2"
            data-testid="add-eval-group-manage"
          >
            Manage groups ↗
          </Link>
        </Box>
      </Popover>
    </>
  );
}

AddEvalGroupButton.propTypes = {
  existing: PropTypes.array,
  onAdd: PropTypes.func,
  onPick: PropTypes.func,
  disabled: PropTypes.bool,
};
