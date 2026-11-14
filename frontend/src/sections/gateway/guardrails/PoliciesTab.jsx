import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import EmptyState from "src/components/empty-state";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { ConfirmDialog } from "src/components/custom-dialog";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  useGuardrailPolicies,
  useUpdateGuardrailPolicy,
  useDeleteGuardrailPolicy,
} from "./hooks/useGuardrailPolicies";
import PolicyDialog from "./PolicyDialog";
import { DORMANT_POLICY_LABEL, isDormantPolicy } from "./policyStatus";

export default function PoliciesTab({ gatewayId }) {
  const {
    data: policies = [],
    isLoading,
    isError,
    refetch,
  } = useGuardrailPolicies();
  const update = useUpdateGuardrailPolicy();
  const remove = useDeleteGuardrailPolicy();
  const [searchParams, setSearchParams] = useSearchParams();
  const [editing, setEditing] = useState(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [menu, setMenu] = useState(null);
  // Only key policies are managed here. Org-wide policies (from the API) are
  // merged into the org's checks, which the Overview and Rules tabs manage;
  // saving one from here would replace those checks.
  const visible = useMemo(
    () =>
      policies.filter(
        (policy) => policy.scope === "key" && !policy.name?.startsWith("__"),
      ),
    [policies],
  );

  useEffect(() => {
    const id = searchParams.get("policy");
    if (id && !isLoading && policies.length) {
      const selected = visible.find((policy) => String(policy.id) === id);
      if (selected) setEditing(selected);
    }
  }, [searchParams, isLoading, policies, visible]);

  const close = () => {
    setEditing(null);
    setCreating(false);
    if (searchParams.has("policy")) {
      const next = new URLSearchParams(searchParams);
      next.delete("policy");
      setSearchParams(next, { replace: true });
    }
  };
  const toggle = async (policy) => {
    try {
      const result = await update.mutateAsync({
        id: policy.id,
        is_active: !policy.is_active,
      });
      enqueueSnackbar(
        policy.is_active ? "Policy deactivated" : "Policy activated",
        { variant: "success" },
      );
      if (result?.gateway_synced === false)
        enqueueSnackbar(result.gateway_warning || "Gateway sync is pending", {
          variant: "warning",
        });
    } catch {
      enqueueSnackbar("Could not update policy", { variant: "error" });
    }
  };
  const deletePolicy = async () => {
    try {
      const result = await remove.mutateAsync(deleting.id);
      setDeleting(null);
      enqueueSnackbar("Policy deleted", { variant: "success" });
      if (result?.gateway_synced === false)
        enqueueSnackbar(result.gateway_warning || "Gateway sync is pending", {
          variant: "warning",
        });
    } catch {
      enqueueSnackbar("Could not delete policy", { variant: "error" });
    }
  };

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 2 }}
      >
        <Typography variant="m3">Policies</Typography>
        {visible.length > 0 && (
          <Button
            variant="contained"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" width={16} />}
            onClick={() => setCreating(true)}
          >
            New policy
          </Button>
        )}
      </Stack>
      {isError ? (
        <ErrorState title="Could not load policies" onRetry={refetch} />
      ) : isLoading ? (
        <ContentSkeleton variant="rows" />
      ) : visible.length === 0 ? (
        <EmptyState
          variant="first-run"
          icon="solar:shield-check-linear"
          title="No policies yet"
          description="Create a policy to monitor or enforce guardrails for chosen API keys. Org-wide checks are set on the Overview and Rules tabs."
          action={
            <Button
              variant="contained"
              size="small"
              onClick={() => setCreating(true)}
            >
              New policy
            </Button>
          }
        />
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell>Name</TableCell>
              <TableCell>Keys</TableCell>
              <TableCell>Mode</TableCell>
              <TableCell>Active</TableCell>
              <TableCell>Updated</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {visible.map((policy) => (
              <TableRow
                key={policy.id}
                hover
                sx={{ height: ROW_HEIGHT.default }}
                data-testid={`guardrail-policy-row-${policy.id}`}
              >
                <TableCell>
                  <Typography variant="s1">{policy.name}</Typography>
                </TableCell>
                <TableCell>{policy.applied_keys?.length || 0}</TableCell>
                <TableCell>
                  <StatusBadge
                    status={policy.mode}
                    label={policy.mode === "monitor" ? "Monitor" : "Enforce"}
                    role={policy.mode === "monitor" ? "info" : "warning"}
                  />
                </TableCell>
                <TableCell>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Switch
                      size="small"
                      aria-label={`Active ${policy.name}`}
                      checked={policy.is_active}
                      disabled={update.isPending}
                      onChange={() => toggle(policy)}
                    />
                    {isDormantPolicy(policy) && (
                      <StatusBadge
                        status="not_running"
                        label={DORMANT_POLICY_LABEL}
                        role="neutral"
                        data-testid={`guardrail-policy-dormant-${policy.id}`}
                      />
                    )}
                  </Stack>
                </TableCell>
                <TableCell>
                  {policy.updated_at
                    ? new Date(policy.updated_at).toLocaleString()
                    : "—"}
                </TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    aria-label={`Actions for ${policy.name}`}
                    onClick={(event) =>
                      setMenu({ anchor: event.currentTarget, policy })
                    }
                  >
                    <Iconify icon="solar:menu-dots-bold" width={16} />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Menu
        anchorEl={menu?.anchor}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
      >
        <MenuItem
          onClick={() => {
            setEditing(menu.policy);
            setMenu(null);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:pen-linear" width={16} />
          </ListItemIcon>
          Edit
        </MenuItem>
        <MenuItem
          onClick={() => {
            setDeleting(menu.policy);
            setMenu(null);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          Delete
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        title="Delete policy?"
        content={`Delete ${deleting?.name || "this policy"}? This cannot be undone.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            disabled={remove.isPending}
            onClick={deletePolicy}
          >
            Delete
          </Button>
        }
      />
      <PolicyDialog
        open={creating || Boolean(editing)}
        policy={editing}
        gatewayId={gatewayId}
        onClose={close}
      />
    </Box>
  );
}

PoliciesTab.propTypes = { gatewayId: PropTypes.string };
