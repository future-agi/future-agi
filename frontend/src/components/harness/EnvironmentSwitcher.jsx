import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Divider,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { formatDistanceToNow } from "date-fns";

import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import {
  ICON_GUTTER,
  ICON_SIZE,
  agentTypeIcon,
  environmentName,
  readable,
  stageStatus,
} from "src/pages/dashboard/harness/harnessShared";

const relativeUpdated = (status) => {
  if (!status?.updated_at) return null;
  const updatedAt = new Date(status.updated_at);
  if (Number.isNaN(updatedAt.getTime())) return null;
  return formatDistanceToNow(updatedAt, { addSuffix: true });
};

export default function EnvironmentSwitcher({
  jobs,
  currentJobId,
  currentName,
  onSelect,
  onCreate,
  showCreate = true,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const close = () => setAnchorEl(null);

  const current = jobs.find((item) => item.job?.job_id === currentJobId);
  const label = environmentName(current?.job, currentName || "RL environment");

  // A cold load of a detail URL can leave the list request unresolved or failed. Without
  // environments to switch between, a dropdown would open onto nothing but the create row,
  // so degrade to a plain label and let the page's own controls carry the create action.
  if (!jobs.length) {
    return (
      <Stack
        direction="row"
        alignItems="center"
        sx={{ gap: `${ICON_GUTTER}px` }}
      >
        <SvgColor
          src={agentTypeIcon(current).src}
          sx={{ width: ICON_SIZE, height: ICON_SIZE, flexShrink: 0 }}
        />
        <Typography variant="subtitle1" noWrap>
          {label}
        </Typography>
      </Stack>
    );
  }

  return (
    <>
      <Button
        variant="outlined"
        color="inherit"
        onClick={(event) => setAnchorEl(event.currentTarget)}
        aria-label="Switch RL environment"
        endIcon={<Iconify icon="eva:chevron-down-fill" width={18} />}
        sx={{
          borderColor: "divider",
          maxWidth: 340,
          px: 1,
          "& .MuiButton-endIcon": { ml: 0.75 },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          sx={{ minWidth: 0, gap: 0.75 }}
        >
          <SvgColor
            src={agentTypeIcon(current).src}
            sx={{ width: ICON_SIZE, height: ICON_SIZE, flexShrink: 0 }}
          />
          <Typography variant="subtitle2" noWrap>
            {label}
          </Typography>
        </Stack>
      </Button>

      <Menu
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 320, maxWidth: 420 } } }}
      >
        {/* The environments scroll; the create row below the divider does not, so it stays
            the last thing in the menu however many environments exist. */}
        <Box sx={{ maxHeight: 320, overflowY: "auto" }}>
          {jobs.map((item) => {
            const jobId = item.job.job_id;
            const typeIcon = agentTypeIcon(item);
            const updated = relativeUpdated(item.status);
            return (
              <MenuItem
                key={jobId}
                selected={jobId === currentJobId}
                onClick={() => {
                  close();
                  onSelect(jobId);
                }}
                sx={{ gap: `${ICON_GUTTER}px`, py: 1 }}
              >
                <SvgColor
                  src={typeIcon.src}
                  sx={{ width: ICON_SIZE, height: ICON_SIZE, flexShrink: 0 }}
                />
                <Stack sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="body2" noWrap>
                    {environmentName(item.job)}
                  </Typography>
                  {updated && (
                    <Typography variant="caption" color="text.secondary" noWrap>
                      Updated {updated}
                    </Typography>
                  )}
                </Stack>
                <StatusChip
                  label={readable(item.status?.stage)}
                  status={stageStatus(item.status?.stage)}
                />
              </MenuItem>
            );
          })}
        </Box>

        {/* Suppressed on the create route itself, where the row would lead back here. */}
        {showCreate && <Divider sx={{ my: 0.5 }} />}
        {showCreate && (
          <MenuItem
            onClick={() => {
              close();
              onCreate();
            }}
            sx={{ gap: `${ICON_GUTTER}px`, py: 1, color: "primary.main" }}
          >
            <Iconify icon="mingcute:add-line" width={ICON_SIZE} />
            <Typography variant="body2" fontWeight={600}>
              Create RL environment
            </Typography>
          </MenuItem>
        )}
      </Menu>
    </>
  );
}

EnvironmentSwitcher.propTypes = {
  jobs: PropTypes.array.isRequired,
  currentJobId: PropTypes.string,
  currentName: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  showCreate: PropTypes.bool,
};
