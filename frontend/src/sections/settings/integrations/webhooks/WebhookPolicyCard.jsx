import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  Chip,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import {
  useEgressIps,
  useUpdatePolicy,
  useWebhookPolicy,
} from "src/api/webhooks";
import { copyToClipboard } from "src/utils/utils";
import { apiError } from "./form";

// The webhook policy card (DESIGN.md section 11.2): allowed hosts (chips,
// leftmost wildcards), "Members can manage webhooks", "Allow prompt text in
// webhook payloads" (org only) and, on hosted, the published sending addresses.
// The org policy on the org settings page; the workspace's own on its page.
export default function WebhookPolicyCard({ access, workspaceRouteId }) {
  const workspaceId = workspaceRouteId || null;
  const canEdit = workspaceId ? access.can_admin : access.org_admin;
  const { data: policy } = useWebhookPolicy(workspaceId);
  const { data: egress } = useEgressIps();
  const update = useUpdatePolicy(workspaceId);
  const [hosts, setHosts] = useState([]);
  const [mcm, setMcm] = useState(false);
  const [full, setFull] = useState(false);
  const [saved, setSaved] = useState(null);

  useEffect(() => {
    if (!policy) return;
    setHosts(policy.allowed_hosts || []);
    setMcm(!!policy.members_can_manage);
    setFull(!!policy.full_payload_allowed);
  }, [policy]);

  if (!policy) return null;
  const dirty =
    JSON.stringify(hosts) !== JSON.stringify(policy.allowed_hosts || []) ||
    mcm !== !!policy.members_can_manage ||
    (!workspaceId && full !== !!policy.full_payload_allowed);

  const save = () => {
    setSaved(null);
    const body = { allowed_hosts: hosts, members_can_manage: mcm };
    if (!workspaceId) body.full_payload_allowed = full;
    update.mutate(body, {
      onSuccess: (res) => setSaved(res.data.result),
    });
  };

  const addresses = (egress?.regions || []).flatMap((r) => r.addresses);

  return (
    <Card
      variant="outlined"
      sx={{ p: 2.5, mt: 3 }}
      component="section"
      aria-label="Webhook policy"
    >
      <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
        {workspaceId ? "Webhook policy for this workspace" : "Webhook policy"}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
        Where webhooks may send, and who may manage them.
      </Typography>
      <Stack spacing={1.5}>
        <Autocomplete
          multiple
          freeSolo
          options={[]}
          value={hosts}
          disabled={!canEdit}
          onChange={(_e, v) =>
            setHosts(v.map((h) => h.trim().toLowerCase()).filter(Boolean))
          }
          renderTags={(value, getTagProps) =>
            value.map((h, i) => (
              <Chip
                size="small"
                label={h}
                {...getTagProps({ index: i })}
                key={h}
              />
            ))
          }
          renderInput={(params) => (
            <TextField
              {...params}
              size="small"
              label="Allowed hosts"
              placeholder={
                hosts.length
                  ? ""
                  : "Any public host. Add hooks.example.com or *.example.com"
              }
              helperText="Type a host and press Enter. Empty means any public host."
            />
          )}
        />
        {workspaceId && policy.effective?.org_allowed_hosts?.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            Your organization allows:{" "}
            {policy.effective.org_allowed_hosts.join(", ")}
          </Typography>
        )}
        <FormControlLabel
          control={
            <Switch
              checked={mcm}
              disabled={!canEdit}
              onChange={(e) => setMcm(e.target.checked)}
            />
          }
          label="Members can manage webhooks"
        />
        {!workspaceId && (
          <FormControlLabel
            control={
              <Switch
                checked={full}
                disabled={!canEdit}
                onChange={(e) => setFull(e.target.checked)}
              />
            }
            label="Allow prompt text in webhook payloads"
          />
        )}
        {egress?.hosted && addresses.length > 0 && (
          <Box>
            <Typography variant="subtitle2">Our sending addresses</Typography>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                {addresses.join(", ")}
              </Typography>
              <Button
                size="small"
                onClick={() => copyToClipboard(addresses.join("\n"))}
              >
                Copy
              </Button>
            </Stack>
          </Box>
        )}
        {update.isError && (
          <Alert severity="error">
            {apiError(update.error, "Could not save the policy.").message}
          </Alert>
        )}
        {saved && (
          <Alert severity="success" role="status">
            Policy saved.
            {saved.blocked_endpoint_ids?.length
              ? ` ${saved.blocked_endpoint_ids.length} webhook(s) now send to a host outside the list and are blocked.`
              : ""}
            {saved.downgraded_endpoint_ids?.length
              ? ` ${saved.downgraded_endpoint_ids.length} webhook(s) no longer include prompt text.`
              : ""}
          </Alert>
        )}
        {canEdit && (
          <Box>
            <Button
              variant="contained"
              size="small"
              disabled={!dirty || update.isPending}
              onClick={save}
            >
              Save policy
            </Button>
          </Box>
        )}
      </Stack>
    </Card>
  );
}

WebhookPolicyCard.propTypes = {
  access: PropTypes.object.isRequired,
  workspaceRouteId: PropTypes.string,
};
