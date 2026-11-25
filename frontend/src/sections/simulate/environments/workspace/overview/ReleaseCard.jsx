import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useEnvironmentVersions,
  useLockRelease,
  useUnlockRelease,
} from "src/api/simulate-environments/versions";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import SectionCard from "../../components/SectionCard";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { RELEASE_COPY, versionLine } from "./overview.constants";

/*
  An environment release, after Vel's ReleasePanel: the default environment and
  scenario versions, locked so scores on them stay comparable. A lock freezes a
  pair of versions, not the environment: an edit makes a new, unlocked version.
  Each task keeps the split it got at its first lock.
*/

const shortDate = (at) =>
  new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export default function ReleaseCard({ envId }) {
  const { data } = useEnvironmentVersions(envId);
  const { canEdit, canUnlockRelease } = useSimulationAccess();
  const lockRelease = useLockRelease(envId);
  const unlockRelease = useUnlockRelease(envId);
  const [confirm, setConfirm] = useState(null);
  if (!data?.lock) return null;

  const { lock, releases, defaults } = data;
  const current = releases.find(
    (release) =>
      !release.unlockedAt &&
      release.environmentVersionId === lock.environmentVersionId &&
      release.suiteVersionId === lock.suiteVersionId,
  );
  const earlier = releases.filter((release) => release !== current);
  const status = current
    ? { label: RELEASE_COPY.status.locked, role: "neutral" }
    : lock.canLock
      ? { label: RELEASE_COPY.status.ready, role: "success" }
      : { label: RELEASE_COPY.status.blocked, role: "warning" };
  const line = current
    ? RELEASE_COPY.lockedLine(
        shortDate(current.lockedAt),
        current.train,
        current.test,
        current.environmentLabel,
      )
    : lock.canLock
      ? RELEASE_COPY.readyLine(lock.train, lock.test)
      : lock.refusal?.message;
  const failed = (error) =>
    enqueueSnackbar(errorMessage(error), { variant: "error" });
  const onLock = () => {
    setConfirm(null);
    lockRelease.mutate(
      {
        environmentVersionId: lock.environmentVersionId,
        suiteVersionId: lock.suiteVersionId,
      },
      { onError: failed },
    );
  };
  const onUnlock = () => {
    setConfirm(null);
    unlockRelease.mutate({ releaseId: current.id }, { onError: failed });
  };

  const cell = { typography: "s1", py: 1, px: 1.5 };
  const head = {
    typography: "s3",
    color: "text.tertiary",
    py: 0.75,
    px: 1.5,
    fontWeight: "fontWeightMedium",
  };

  return (
    <SectionCard
      title={RELEASE_COPY.title(current?.label)}
      subtitle={
        current ? RELEASE_COPY.subtitleLocked : RELEASE_COPY.subtitleOpen
      }
      action={
        <StatusBadge
          status={status.label}
          label={status.label}
          role={status.role}
        />
      }
      sx={{ mb: 3 }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.secondary",
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {versionLine(defaults)}
      </Typography>
      <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
        <Box component="thead">
          <Box component="tr" sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Box component="th" sx={{ ...head, textAlign: "left", width: 180 }}>
              {RELEASE_COPY.head.check}
            </Box>
            <Box component="th" sx={{ ...head, textAlign: "left" }}>
              {RELEASE_COPY.head.proves}
            </Box>
            <Box
              component="th"
              sx={{ ...head, textAlign: "right", width: 120 }}
            >
              {RELEASE_COPY.head.result}
            </Box>
          </Box>
        </Box>
        <Box component="tbody">
          <Box component="tr" sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Box
              component="td"
              sx={{ ...cell, fontWeight: "fontWeightMedium" }}
            >
              {RELEASE_COPY.reference.label}
            </Box>
            <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
              {RELEASE_COPY.reference.proves}
            </Box>
            <Box
              component="td"
              sx={{
                ...cell,
                textAlign: "right",
                color: lock.proved ? "status.success.fg" : "text.tertiary",
              }}
            >
              {lock.proved
                ? RELEASE_COPY.reference.passed
                : RELEASE_COPY.reference.unproved}
            </Box>
          </Box>
        </Box>
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 1.5, py: 1.25 }}
      >
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", flex: 1, minWidth: 0 }}
        >
          {line}
        </Typography>
        {current ? (
          canUnlockRelease && (
            <Button
              size="small"
              variant="outlined"
              disabled={unlockRelease.isPending}
              onClick={() => setConfirm("unlock")}
              startIcon={
                <Iconify icon="solar:lock-keyhole-unlocked-linear" width={14} />
              }
              sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              {RELEASE_COPY.unlock}
            </Button>
          )
        ) : (
          <Tooltip arrow title={lock.canLock ? "" : lock.refusal?.message || ""}>
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!canEdit || !lock.canLock || lockRelease.isPending}
                onClick={() => setConfirm("lock")}
                startIcon={<Iconify icon="solar:lock-keyhole-linear" width={14} />}
                sx={{ flexShrink: 0 }}
              >
                {RELEASE_COPY.lock}
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      {earlier.length > 0 && (
        <Box sx={{ borderTop: 1, borderColor: "divider", px: 1.5, py: 1.25 }}>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
          >
            {RELEASE_COPY.earlier}
          </Typography>
          {earlier.map((release) => (
            <Typography
              key={release.id}
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {RELEASE_COPY.earlierLine(release)}
            </Typography>
          ))}
        </Box>
      )}

      {confirm === "lock" && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={RELEASE_COPY.lockTitle}
          content={RELEASE_COPY.lockBody}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onLock}
            >
              {RELEASE_COPY.lockAction}
            </Button>
          }
        />
      )}
      {confirm === "unlock" && current && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={RELEASE_COPY.unlockTitle(current.label)}
          content={RELEASE_COPY.unlockBody}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onUnlock}
            >
              {RELEASE_COPY.unlockAction}
            </Button>
          }
        />
      )}
    </SectionCard>
  );
}

ReleaseCard.propTypes = {
  envId: PropTypes.string.isRequired,
};
