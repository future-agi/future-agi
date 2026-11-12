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
import React, { useId } from "react";
import { enqueueSnackbar } from "src/components/snackbar";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { dimExceptLine } from "src/components/NotInRelease/notInRelease";
import {
  useSharingPolicyQuery,
  useUpdateSharingPolicy,
} from "src/api/develop/dataset-access";

// Whether this build serves the sharing policy: while false the card shows its controls disabled with the release line and asks the server nothing. The card's policyServed prop exists only so tests can render the served card.
const SHARING_POLICY_SERVED = false;

const errorText = (e, fallback) =>
  e?.error || e?.result || e?.message || fallback;

// The card frame of the sibling Settings pages (Organization settings,
// Profile), so this card lines up with the one above it.
const cardSx = {
  width: "100%",
  maxWidth: "670px",
  p: 2,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: (theme) => theme.spacing(1),
  bgcolor: "background.paper",
};

// The section-card title of the sibling Settings pages (Organization
// settings' security card): s1, semibold, primary text.
const titleSx = { fontWeight: "fontWeightSemiBold", color: "text.primary" };

const publicLinksLabel = (
  <Box sx={{ pt: 0.75 }}>
    <Typography variant="s1" fontWeight="fontWeightSemiBold" component="span">
      Public links
    </Typography>
    <Typography variant="s1" color="text.secondary" component="span">
      {" · Allow public, read-only links to datasets"}
    </Typography>
  </Box>
);

// The card as it shows while the policy is not served: both controls on
// screen, off and disabled, and the shared release line saying why. The
// control rows are dimmed evenly by the shared disabled-card helper, so the
// switch label keeps its colour instead of greying on its own; the title and
// the release line stay at full strength.
function SharingNotInRelease() {
  const titleId = useId();
  const lineId = useId();
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid="workspace-sharing-card"
      sx={cardSx}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1}
        sx={{ flexWrap: "wrap", rowGap: 1 }}
      >
        <Typography id={titleId} component="h2" variant="s1" sx={titleSx}>
          Sharing
        </Typography>
        <Box component="span" id={lineId} sx={{ display: "inline-flex" }}>
          <NotInReleaseChip />
        </Box>
      </Stack>
      <Box sx={dimExceptLine}>
        <FormControlLabel
          sx={{
            mt: 1,
            ml: 0,
            alignItems: "flex-start",
            "& .MuiFormControlLabel-label.Mui-disabled": {
              color: "text.primary",
            },
          }}
          control={
            <Switch
              checked={false}
              disabled
              inputProps={{
                "aria-label": "Public links",
                "aria-describedby": lineId,
              }}
            />
          }
          label={publicLinksLabel}
        />
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ mt: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          <Typography variant="s2">Links expire after at most</Typography>
          <Select
            size="small"
            value=""
            disabled
            inputProps={{
              "aria-label": "Longest a public link lasts, in days",
              "aria-describedby": lineId,
            }}
            sx={{
              typography: "s2",
              minWidth: 64,
              "& .MuiSelect-select": { py: 0.5 },
            }}
          />
          <Typography variant="s2">days</Typography>
          <Typography variant="s3" color="text.secondary">
            · links made before close at this limit too
          </Typography>
        </Stack>
      </Box>
    </Box>
  );
}

function SharingPolicyCard({ workspaceId }) {
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
    <Box data-testid="workspace-sharing-card" sx={cardSx}>
      <Typography component="h2" variant="s1" sx={titleSx}>
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
        label={publicLinksLabel}
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

SharingPolicyCard.propTypes = { workspaceId: PropTypes.string };

export default function WorkspaceSharingCard({
  workspaceId,
  policyServed = SHARING_POLICY_SERVED,
}) {
  if (!policyServed) return <SharingNotInRelease />;
  return <SharingPolicyCard workspaceId={workspaceId} />;
}

WorkspaceSharingCard.propTypes = {
  workspaceId: PropTypes.string,
  policyServed: PropTypes.bool,
};
