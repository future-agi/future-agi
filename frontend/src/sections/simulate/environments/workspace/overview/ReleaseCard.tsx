import { useState, type ComponentType } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import IconifyJs from "src/components/iconify";
import StatusBadgeJs from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useEnvironmentVersions,
  useLockRelease as useLockReleaseJs,
  useUnlockRelease as useUnlockReleaseJs,
} from "src/api/simulate-environments/versions";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import SectionCard from "../../components/SectionCard";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import ViewerLock from "../../components/ViewerLock";
import { RELEASE_COPY, versionLine } from "./overview.constants";

/*
  An environment release, after Vel's ReleasePanel: the default environment and
  scenario versions, locked so scores on them stay comparable. A lock freezes a
  pair of versions, not the environment: an edit makes a new, unlocked version.
  Each task keeps the split it got at its first lock.
*/

// A release as useEnvironmentVersions maps it (versions.js::releaseFromApi).
export interface Release {
  id: string;
  label: string;
  environmentVersionId: string;
  environmentLabel: string | null;
  suiteVersionId: string;
  suiteLabel: string | null;
  lockedAt: string | null;
  lockedBy: string | null;
  unlockedAt: string | null;
  unlockedBy: string | null;
  train: number;
  test: number;
}

// The default pair a lock names.
export interface VersionPair {
  environmentVersionId: string | null;
  suiteVersionId: string | null;
}

// Whether the default pair can be locked (versions.js::lockFromApi).
interface LockPreview extends VersionPair {
  proved: boolean;
  canLock: boolean;
  refusal: { code: string; message: string } | null;
  train: number | null;
  test: number | null;
  testTarget: number | null;
}

// What the open confirmation acts on: the pair it opened with, or a release.
type Confirm = { lock: VersionPair } | { unlock: Release } | null;

interface ReleaseCardProps {
  envId: string;
  // Jumps to a workspace tab, for the refusals the author fixes there.
  onGo?: (tab: string) => void;
}

interface UnlockButtonProps {
  disabled?: boolean;
  onClick: () => void;
}

// The JS modules this card uses, typed here for what it passes them.
interface ReleaseMutation<Variables> {
  isPending: boolean;
  mutate: (
    variables: Variables,
    options: { onError: (error: unknown) => void },
  ) => void;
}
const useLockRelease = useLockReleaseJs as unknown as (
  envId: string,
) => ReleaseMutation<VersionPair>;
const useUnlockRelease = useUnlockReleaseJs as unknown as (
  envId: string,
) => ReleaseMutation<{ releaseId: string }>;
const Iconify = IconifyJs as unknown as ComponentType<{
  icon: string;
  width: number;
}>;
const StatusBadge = StatusBadgeJs as unknown as ComponentType<{
  status: string;
  label: string;
  role: string;
}>;

// Refusals the author fixes by adding scenarios.
const ADD_SCENARIOS_FOR = ["release_too_few_test", "release_too_few_train"];

const shortDate = (at: string | null) =>
  new Date(at ?? 0).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });

export default function ReleaseCard({ envId, onGo }: ReleaseCardProps) {
  const { data } = useEnvironmentVersions(envId);
  const { canEdit, canUnlockRelease } = useSimulationAccess();
  const lockRelease = useLockRelease(envId);
  const unlockRelease = useUnlockRelease(envId);
  const [confirm, setConfirm] = useState<Confirm>(null);
  if (!data?.lock) return null;

  const lock: LockPreview = data.lock;
  const releases: Release[] = data.releases;
  const { defaults } = data;
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
  const failed = (error: unknown) =>
    enqueueSnackbar(errorMessage(error), { variant: "error" });
  // Locks the pair the dialog was opened on, which a refresh may since have moved.
  const onLock = (pair: VersionPair) => {
    setConfirm(null);
    lockRelease.mutate(pair, { onError: failed });
  };
  // Any locked release can be unlocked, not only the default pair's: an edit moves
  // the default on, and delete still waits for every lock to go.
  const onUnlock = (release: Release) => {
    setConfirm(null);
    unlockRelease.mutate({ releaseId: release.id }, { onError: failed });
  };
  const needsScenarios = ADD_SCENARIOS_FOR.includes(lock.refusal?.code ?? "");
  const confirmLock = confirm && "lock" in confirm ? confirm.lock : null;
  const confirmUnlock = confirm && "unlock" in confirm ? confirm.unlock : null;

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
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {line}
        </Typography>
        {current ? (
          canUnlockRelease && (
            <UnlockButton
              disabled={unlockRelease.isPending}
              onClick={() => setConfirm({ unlock: current })}
            />
          )
        ) : (
          <>
            {needsScenarios && onGo && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onGo("scenarios")}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                {RELEASE_COPY.addScenarios}
              </Button>
            )}
            <ViewerLock locked={!canEdit} title={RELEASE_COPY.viewer}>
              <Tooltip
                arrow
                title={
                  canEdit && !lock.canLock ? lock.refusal?.message || "" : ""
                }
              >
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={
                      !canEdit || !lock.canLock || lockRelease.isPending
                    }
                    onClick={() =>
                      setConfirm({
                        lock: {
                          environmentVersionId: lock.environmentVersionId,
                          suiteVersionId: lock.suiteVersionId,
                        },
                      })
                    }
                    startIcon={
                      <Iconify icon="solar:lock-keyhole-linear" width={14} />
                    }
                    sx={{ flexShrink: 0 }}
                  >
                    {RELEASE_COPY.lock}
                  </Button>
                </span>
              </Tooltip>
            </ViewerLock>
          </>
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
            <Stack
              key={release.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ minHeight: 28 }}
            >
              <Typography
                sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
              >
                {RELEASE_COPY.earlierLine(release)}
              </Typography>
              {!release.unlockedAt && canUnlockRelease && (
                <UnlockButton
                  disabled={unlockRelease.isPending}
                  onClick={() => setConfirm({ unlock: release })}
                />
              )}
            </Stack>
          ))}
        </Box>
      )}

      {confirmLock && (
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
              onClick={() => onLock(confirmLock)}
            >
              {RELEASE_COPY.lockAction}
            </Button>
          }
        />
      )}
      {confirmUnlock && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={RELEASE_COPY.unlockTitle(confirmUnlock.label)}
          content={RELEASE_COPY.unlockBody}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => onUnlock(confirmUnlock)}
            >
              {RELEASE_COPY.unlockAction}
            </Button>
          }
        />
      )}
    </SectionCard>
  );
}

function UnlockButton({ disabled, onClick }: UnlockButtonProps) {
  return (
    <Button
      size="small"
      variant="outlined"
      disabled={disabled}
      onClick={onClick}
      startIcon={
        <Iconify icon="solar:lock-keyhole-unlocked-linear" width={14} />
      }
      sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
    >
      {RELEASE_COPY.unlock}
    </Button>
  );
}
