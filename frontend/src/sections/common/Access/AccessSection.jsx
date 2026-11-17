// Dataset settings › Access (W4-5; W4-SCOPE §6 A1). Every change saves at
// once (PUT) and the server audits it. Admins edit; everyone else reads it
// with their own line and a "Request … access" button. Levels, the
// workspace name, the people and the admins to ask all come from the server.
// Groups are absent: there is no groups model.
import {
  Autocomplete,
  Avatar,
  Box,
  Button,
  Divider,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useOrgMembers } from "src/api/annotation-queues/annotation-queues";
import { addablePeople } from "./accessModel";
import {
  useDatasetAccessQuery,
  useRequestDatasetAccess,
  useUpdateDatasetAccess,
} from "src/api/develop/dataset-access";
import { fToNow } from "src/utils/format-time";
import AuditDrawer from "src/sections/common/Audit/AuditDrawer";
import StatusLine from "./StatusLine";
import useStatusLine from "./useStatusLine";
import {
  ACCESS_COPY,
  REQUESTED_LINE,
  grantToast,
  levelGranting,
  meLine,
  requestPerm,
  requestLabel,
  requestSentence,
  workspaceToast,
} from "./accessModel";

const initials = (name = "") =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");

const errorText = (e, fallback) =>
  e?.error || e?.result || e?.message || fallback;

function LevelSelect({ value, levels, onChange, disabled, label }) {
  return (
    <Select
      size="small"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      inputProps={{ "aria-label": label }}
      sx={{
        minWidth: 150,
        typography: "s2",
        "& .MuiSelect-select": { py: 0.5 },
      }}
    >
      {levels.map((l) => (
        <MenuItem key={l.key} value={l.key} sx={{ typography: "s2" }}>
          {l.label}
        </MenuItem>
      ))}
    </Select>
  );
}
LevelSelect.propTypes = {
  value: PropTypes.string.isRequired,
  levels: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  label: PropTypes.string,
};

function PersonRow({ person, children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ py: 0.5 }}>
      <Avatar
        sx={{
          width: 26,
          height: 26,
          fontSize: 11,
          bgcolor: "action.selected",
          color: "text.primary",
        }}
      >
        {initials(person?.name)}
      </Avatar>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="s2" sx={{ display: "block" }} noWrap>
          {person?.name}
        </Typography>
        <Typography
          variant="s3"
          color="text.secondary"
          sx={{ display: "block" }}
          noWrap
        >
          {person?.email}
        </Typography>
      </Box>
      {children}
    </Stack>
  );
}
PersonRow.propTypes = { person: PropTypes.object, children: PropTypes.node };

export default function AccessSection({ datasetId }) {
  const { data: view, isLoading } = useDatasetAccessQuery(datasetId);
  const update = useUpdateDatasetAccess(datasetId);
  const ask = useRequestDatasetAccess(datasetId);
  const { currentOrganizationId } = useOrganization() ?? {};
  const [adding, setAdding] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [status, say] = useStatusLine();
  const canManage = Boolean(view?.can_manage);
  const { data: members = [] } = useOrgMembers(currentOrganizationId, {
    enabled: Boolean(currentOrganizationId) && canManage && adding,
  });
  const levels = view?.levels ?? [];
  // A person's grant may also be Manage; the workspace level never is.
  const grantLevels = view?.grant_levels ?? levels;
  const granted = useMemo(
    () => new Set((view?.grants ?? []).map((g) => g.user?.id)),
    [view],
  );
  // Not the signed-in admin, and no workspace admin or owner: they always
  // have full access, so a per-person level would claim a limit that never
  // applies (v2.1 g8, access:grant).
  const addable = addablePeople(members, {
    granted,
    meId: view?.me?.user_id,
  });

  if (isLoading || !view) return null;

  const save = (body, toast) =>
    update.mutate(body, {
      onSuccess: () => say(toast),
      onError: (e) => say(errorText(e, "Couldn't change access."), "error"),
    });
  const setWorkspace = (level) =>
    save(
      { workspace_level: level },
      workspaceToast(view.workspace?.name, level, levels),
    );
  const setGrant = (person, level) =>
    save(
      { grants: [{ user_id: person.id, level }] },
      grantToast(person.name, level, grantLevels),
    );
  // "+ Add people" gives the new person View (the server's `view` level).
  const viewLevel = levels.find((l) => l.key === "view");
  const grantRequest = (req) => {
    const current = view.grants.find((g) => g.user?.id === req.user?.id)?.level;
    const level = levelGranting(req.perm, current, levels);
    if (level) setGrant(req.user, level.key);
  };
  const missing = requestPerm(view.me);
  const myRequest = !canManage && view.requests.find((r) => r.perm === missing);

  return (
    <Box data-testid="dataset-access-section" sx={{ mt: 2 }}>
      <Divider sx={{ mb: 1.5 }} />
      <Typography
        variant="s1"
        fontWeight="fontWeightSemiBold"
        sx={{ display: "block", mb: 1 }}
      >
        {ACCESS_COPY.title}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Typography variant="s2">
          Everyone in {view.workspace?.name} can
        </Typography>
        {canManage ? (
          <LevelSelect
            value={view.workspace_level}
            levels={levels}
            onChange={setWorkspace}
            disabled={update.isPending}
            label={`Access for everyone in ${view.workspace?.name}`}
          />
        ) : (
          <Typography variant="s2" fontWeight="fontWeightSemiBold">
            {levels.find((l) => l.key === view.workspace_level)?.label}
          </Typography>
        )}
      </Stack>
      <Typography
        variant="s3"
        color="text.secondary"
        sx={{ display: "block", mt: 0.75 }}
      >
        {ACCESS_COPY.adminsNote}
      </Typography>
      {view.workspace_level === "none" && (
        <Typography
          variant="s3"
          sx={{ display: "block", mt: 0.75, color: "warning.dark" }}
        >
          {ACCESS_COPY.restricted}
        </Typography>
      )}
      {view.grants.length > 0 && (
        <Box sx={{ mt: 1 }} data-testid="dataset-access-people">
          {view.grants.map((g) => (
            <PersonRow key={g.user.id} person={g.user}>
              {canManage ? (
                <>
                  <LevelSelect
                    value={g.level}
                    levels={grantLevels}
                    onChange={(level) => setGrant(g.user, level)}
                    disabled={update.isPending}
                    label={`Access for ${g.user.name}`}
                  />
                  <IconButton
                    size="small"
                    aria-label={`Remove ${g.user.name}`}
                    onClick={() => setGrant(g.user, null)}
                  >
                    <Iconify icon="mingcute:close-line" width={16} />
                  </IconButton>
                </>
              ) : (
                <Typography variant="s2" color="text.secondary">
                  {grantLevels.find((l) => l.key === g.level)?.label}
                </Typography>
              )}
            </PersonRow>
          ))}
        </Box>
      )}
      {canManage && (
        <Box sx={{ mt: 1 }}>
          {adding ? (
            <Autocomplete
              size="small"
              openOnFocus
              options={addable}
              getOptionLabel={(o) => `${o.name} · ${o.email}`}
              onChange={(_, person) => {
                if (person && viewLevel) setGrant(person, viewLevel.key);
                setAdding(false);
              }}
              onBlur={() => setAdding(false)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  autoFocus
                  placeholder="Search people in this organization"
                />
              )}
              noOptionsText="Everyone here already has access listed"
            />
          ) : (
            <Button
              size="small"
              startIcon={<Iconify icon="mingcute:add-line" width={14} />}
              onClick={() => setAdding(true)}
            >
              {ACCESS_COPY.addPeople}
            </Button>
          )}
        </Box>
      )}
      {canManage && view.requests.length > 0 && (
        <Box sx={{ mt: 1.5 }} data-testid="dataset-access-requests">
          {view.requests.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ py: 0.25 }}
            >
              <Typography variant="s2" sx={{ flex: 1 }}>
                {requestSentence(r)} · {fToNow(r.at)}
              </Typography>
              <Button
                size="small"
                variant="contained"
                onClick={() => grantRequest(r)}
                disabled={update.isPending}
              >
                Grant
              </Button>
              <Button
                size="small"
                onClick={() =>
                  save(
                    { dismiss_requests: [r.id] },
                    `Dismissed ${r.user?.name}'s request`,
                  )
                }
                disabled={update.isPending}
              >
                Dismiss
              </Button>
            </Stack>
          ))}
        </Box>
      )}
      {!canManage && (
        <Stack spacing={0.75} sx={{ mt: 1.25 }} data-testid="dataset-access-me">
          <Typography variant="s2">{meLine(view)}</Typography>
          {missing &&
            (myRequest ? (
              <Typography variant="s3" color="text.secondary">
                {REQUESTED_LINE}
              </Typography>
            ) : (
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={ask.isPending}
                  onClick={() =>
                    ask.mutate(missing, {
                      onError: (e) =>
                        say(
                          errorText(e, "Couldn't send the request."),
                          "error",
                        ),
                    })
                  }
                >
                  {requestLabel(missing)}
                </Button>
              </Box>
            ))}
        </Stack>
      )}
      {status && (
        <Box sx={{ mt: 1.25 }}>
          <StatusLine status={status} testId="dataset-access-status" />
        </Box>
      )}
      <Box sx={{ mt: 1.5 }}>
        <Button
          size="small"
          variant="text"
          startIcon={<Iconify icon="mdi:history" width={14} />}
          onClick={() => setAuditOpen(true)}
          sx={{ px: 0.5 }}
        >
          Audit log
        </Button>
      </Box>
      {auditOpen && (
        <AuditDrawer
          open
          onClose={() => setAuditOpen(false)}
          scope={`dataset:${datasetId}`}
          title={view.dataset_name}
        />
      )}
    </Box>
  );
}

AccessSection.propTypes = {
  datasetId: PropTypes.string,
};
