// Settings › Workspace › General › Sharing (W4-6; owner decision 6): the
// switch that allows public, read-only dataset links (off by default) and
// how long a link may last (1 … the server's limit). Admins edit; others
// read it. Every change is audited by the server. The policy governs links
// made before too: while it is off none opens, and a lower maximum closes
// them sooner (the server says how many links it governs, `active_links`).
import {
  Box,
  FormControlLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  useSharingPolicyQuery,
  useUpdateSharingPolicy,
} from "src/api/develop/dataset-access";

const errorText = (e, fallback) =>
  e?.error || e?.result || e?.message || fallback;

export default function WorkspaceSharingCard({ workspaceId }) {
  const { data: policy } = useSharingPolicyQuery(workspaceId);
  const update = useUpdateSharingPolicy(workspaceId);
  if (!policy) return null;
  const canEdit = Boolean(policy.can_edit);
  const save = (body, toast) =>
    update.mutate(body, {
      onSuccess: () => enqueueSnackbar(toast, { variant: "success" }),
      onError: (e) =>
        enqueueSnackbar(errorText(e, "Couldn't change the sharing policy."), {
          variant: "error",
        }),
    });
  const days = Array.from({ length: policy.max_days_limit }, (_, i) => i + 1);
  const held = canEdit && !policy.public_links ? policy.active_links ?? 0 : 0;
  return (
    <Box
      data-testid="workspace-sharing-card"
      sx={{ mt: 4, p: 2, border: 1, borderColor: "divider", borderRadius: 1 }}
    >
      <Typography sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}>
        Sharing
      </Typography>
      <FormControlLabel
        sx={{ mt: 1, ml: 0, alignItems: "flex-start" }}
        control={
          <Switch
            checked={policy.public_links}
            disabled={!canEdit || update.isPending}
            onChange={(e) =>
              save(
                { public_links: e.target.checked },
                e.target.checked
                  ? `Public links are on for ${policy.workspace_name}`
                  : `Public links are off for ${policy.workspace_name}`,
              )
            }
            inputProps={{ "aria-label": "Public links" }}
          />
        }
        label={
          <Box sx={{ pt: 0.75 }}>
            <Typography
              variant="s1"
              fontWeight="fontWeightSemiBold"
              component="span"
            >
              Public links
            </Typography>
            <Typography variant="s1" color="text.secondary" component="span">
              {" · Allow public, read-only links to datasets"}
            </Typography>
          </Box>
        }
      />
      {held > 0 && (
        <Typography
          variant="s3"
          color="warning.dark"
          sx={{ display: "block", mt: 0.5 }}
          data-testid="sharing-held-links"
        >
          {held === 1
            ? "1 public link doesn't open while this is off. Turning it on reopens it unless it has expired."
            : `${held} public links don't open while this is off. Turning it on reopens the ones that haven't expired.`}
        </Typography>
      )}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
        <Typography variant="s2">Links expire after at most</Typography>
        <Select
          size="small"
          value={policy.max_days}
          disabled={!canEdit || update.isPending}
          onChange={(e) =>
            save(
              { max_days: e.target.value },
              `Public links now last at most ${e.target.value} ${e.target.value === 1 ? "day" : "days"}`,
            )
          }
          inputProps={{ "aria-label": "Longest a public link lasts, in days" }}
          sx={{ typography: "s2", "& .MuiSelect-select": { py: 0.5 } }}
        >
          {days.map((d) => (
            <MenuItem key={d} value={d} sx={{ typography: "s2" }}>
              {d}
            </MenuItem>
          ))}
        </Select>
        <Typography variant="s2">days</Typography>
        <Typography variant="s3" color="text.secondary">
          · links made before close at this limit too
        </Typography>
      </Stack>
      {!canEdit && (
        <Typography
          variant="s3"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
        >
          Only workspace admins can change this.
        </Typography>
      )}
    </Box>
  );
}

WorkspaceSharingCard.propTypes = { workspaceId: PropTypes.string };
