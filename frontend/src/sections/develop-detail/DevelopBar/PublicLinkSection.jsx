// Share › Public link (W4-6; W4-SCOPE §6 A3): a public, read-only link to a
// dataset on the platform's one share-link system (/tracer/shared-links/),
// behind the workspace sharing policy. Admins create and turn links off;
// others see whether links are on. Renders nothing until the policy loads.
// While the policy is off no link opens; an admin still sees the dataset's
// active link, marked as not opening, with Turn off.
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  Divider,
  FormControlLabel,
  Link,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import StatusLine from "src/sections/common/Access/StatusLine";
import useStatusLine from "src/sections/common/Access/useStatusLine";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useSharingPolicyQuery } from "src/api/develop/dataset-access";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import {
  useCreateSharedLink,
  useDeleteSharedLink,
  useGetSharedLinks,
} from "src/api/shared-links";
import CopyButton from "src/sections/common/ux/CopyButton";
import {
  anyoneLine,
  defaultExpiry,
  earlierLine,
  expiryChoices,
  expiryLabel,
  isActive,
  pausedLine,
  publicUrl,
  viewsLine,
} from "./publicLinkModel";

const errorText = (e, fallback) =>
  e?.error || e?.result || e?.message || fallback;

function CreateForm({ datasetId, policy, latestNumber, onDone, say }) {
  const create = useCreateSharedLink();
  const [experiments, setExperiments] = useState(false);
  const [feedback, setFeedback] = useState(false);
  const [version, setVersion] = useState("frozen");
  const [days, setDays] = useState(defaultExpiry(policy));
  const choices = expiryChoices(policy);
  const submit = () =>
    create.mutate(
      {
        resource_type: "dataset",
        resource_id: datasetId,
        access_type: "public",
        expires_in_days: days,
        version,
        include: { rows: true, experiments, feedback },
      },
      {
        onSuccess: () => {
          say("Public link created");
          onDone();
        },
        onError: (e) => say(errorText(e, "Couldn't create the link."), "error"),
      },
    );
  return (
    <Box
      data-testid="public-link-form"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Stack direction="row" alignItems="center" flexWrap="wrap">
        <Typography variant="s2" sx={{ mr: 1 }}>
          Shows
        </Typography>
        <FormControlLabel
          control={<Checkbox size="small" checked disabled />}
          label={<Typography variant="s2">Rows</Typography>}
        />
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={experiments}
              onChange={(e) => setExperiments(e.target.checked)}
            />
          }
          label={
            <Typography variant="s2">Experiments and run results</Typography>
          }
        />
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={feedback}
              onChange={(e) => setFeedback(e.target.checked)}
            />
          }
          label={<Typography variant="s2">Human feedback</Typography>}
        />
      </Stack>
      <Stack direction="row" alignItems="center">
        <Typography variant="s2" sx={{ mr: 1 }}>
          Version
        </Typography>
        <RadioGroup
          row
          value={version}
          onChange={(e) => setVersion(e.target.value)}
        >
          <FormControlLabel
            value="frozen"
            control={<Radio size="small" />}
            label={
              <Typography variant="s2">
                {latestNumber != null
                  ? `v${latestNumber}, frozen`
                  : "This version, frozen"}
              </Typography>
            }
          />
          <FormControlLabel
            value="latest"
            control={<Radio size="small" />}
            label={
              <Typography variant="s2">Always the latest version</Typography>
            }
          />
        </RadioGroup>
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="s2">Expires in</Typography>
        <Select
          size="small"
          value={days ?? ""}
          onChange={(e) => setDays(e.target.value)}
          inputProps={{ "aria-label": "Link expires in" }}
          sx={{ typography: "s2", "& .MuiSelect-select": { py: 0.5 } }}
        >
          {choices.map((d) => (
            <MenuItem key={d} value={d} sx={{ typography: "s2" }}>
              {expiryLabel(d)}
            </MenuItem>
          ))}
        </Select>
        <Box sx={{ flex: 1 }} />
        <Button size="small" onClick={onDone}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          onClick={submit}
          disabled={create.isPending || !days}
        >
          Create
        </Button>
      </Stack>
    </Box>
  );
}
CreateForm.propTypes = {
  datasetId: PropTypes.string.isRequired,
  policy: PropTypes.object.isRequired,
  latestNumber: PropTypes.number,
  say: PropTypes.func.isRequired,
  onDone: PropTypes.func.isRequired,
};

export default function PublicLinkSection({ datasetId }) {
  const { currentWorkspaceId } = useWorkspace() ?? {};
  const { data: policy } = useSharingPolicyQuery(currentWorkspaceId);
  // Only admins make and turn off links, so only they read them (also
  // while the policy is off: a link made before still needs Turn off).
  const { data: links = [] } = useGetSharedLinks(
    policy?.can_edit ? "dataset" : null,
    datasetId,
  );
  const { data: versions } = useDatasetVersions(
    policy?.can_edit ? datasetId : null,
  );
  const revoke = useDeleteSharedLink();
  const [creating, setCreating] = useState(false);
  const [showEarlier, setShowEarlier] = useState(false);
  const [status, say] = useStatusLine();
  if (!policy) return null;
  const admin = Boolean(policy.can_edit);
  if (!policy.public_links && !admin) return null;

  const wrap = (children) => (
    <Box data-testid="public-link-section">
      <Divider sx={{ my: 1 }} />
      <Typography
        variant="s1"
        fontWeight="fontWeightSemiBold"
        sx={{ display: "block", mb: 0.75 }}
      >
        Public link
      </Typography>
      {children}
    </Box>
  );

  if (!admin)
    return wrap(
      <Typography variant="s2" color="text.secondary">
        Public links are on for this workspace; an admin creates them.
      </Typography>,
    );

  const off = !policy.public_links;
  const active = links.find((l) => isActive(l));
  const earlier = links.filter((l) => !isActive(l));
  const url = active ? publicUrl(window.location.origin, active) : null;
  return wrap(
    <Stack spacing={1}>
      {off && (
        <Typography
          variant="s2"
          color="text.secondary"
          data-testid="public-link-policy-off"
        >
          {`Public links are off for ${policy.workspace_name}. Turn them on in `}
          <Link
            href={`/dashboard/settings/workspace/${policy.workspace_id}/general`}
            underline="always"
          >
            Workspace settings › General
          </Link>
        </Typography>
      )}
      {active ? (
        <>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <TextField
              size="small"
              value={url}
              fullWidth
              InputProps={{
                readOnly: true,
                sx: { typography: "s2", fontFamily: "monospace" },
              }}
              inputProps={{ "aria-label": "Public link" }}
              onFocus={(e) => e.target.select()}
            />
            <CopyButton value={url} tooltip="Copy public link" />
          </Box>
          <Typography
            variant="s2"
            color={off ? "warning.dark" : "text.secondary"}
            data-testid="public-link-line"
          >
            {off
              ? pausedLine(active, policy.workspace_name)
              : anyoneLine(active)}
          </Typography>
          <Stack direction="row" alignItems="center">
            <Typography
              variant="s3"
              color="text.secondary"
              sx={{ flex: 1 }}
              data-testid="public-link-views"
            >
              {viewsLine(active)}
            </Typography>
            <Button
              size="small"
              color="error"
              disabled={revoke.isPending}
              onClick={() =>
                revoke.mutate(active.id, {
                  onSuccess: () => say("Public link turned off"),
                  onError: (e) =>
                    say(errorText(e, "Couldn't turn the link off."), "error"),
                })
              }
            >
              Turn off
            </Button>
          </Stack>
        </>
      ) : off ? null : creating ? (
        <CreateForm
          datasetId={datasetId}
          policy={policy}
          latestNumber={latestOf(versions)?.number}
          onDone={() => setCreating(false)}
          say={say}
        />
      ) : (
        <Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setCreating(true)}
          >
            Create public link
          </Button>
        </Box>
      )}
      {earlier.length > 0 && (
        <Box>
          <Button
            size="small"
            variant="text"
            onClick={() => setShowEarlier((v) => !v)}
            sx={{ px: 0.5 }}
          >
            {`Earlier links (${earlier.length})`}
          </Button>
          <Collapse in={showEarlier}>
            <Stack spacing={0.5} sx={{ pl: 1 }}>
              {earlier.map((l) => (
                <Typography key={l.id} variant="s3" color="text.secondary">
                  {earlierLine(l)}
                </Typography>
              ))}
            </Stack>
          </Collapse>
        </Box>
      )}
      <StatusLine status={status} testId="public-link-status" />
    </Stack>,
  );
}

PublicLinkSection.propTypes = { datasetId: PropTypes.string };
