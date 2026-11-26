import { useState, type ComponentType } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import IconifyJs from "src/components/iconify";
import StatusBadgeJs from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  useEnvironmentVersions,
  useLockPreview as useLockPreviewJs,
  useLockVersion as useLockVersionJs,
  useUnlockVersion as useUnlockVersionJs,
} from "src/api/simulate-environments/versions";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import SectionCard from "../../components/SectionCard";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import ViewerLock from "../../components/ViewerLock";
import { LOCK_COPY, lockPair, versionLine } from "./overview.constants";

/*
  An environment's locked version, after Vel's design: the default environment and
  scenario versions, locked so scores on them stay comparable. A lock freezes a
  pair of versions, not the environment: an edit makes a new, unlocked version.
  Each task keeps the split it got at its first lock.
*/

// A locked version as useEnvironmentVersions maps it (versions.js::lockedVersionFromApi).
export interface LockedVersion {
  id: string;
  label: string;
  environmentVersionId: string;
  environmentLabel: string | null;
  suiteVersionId: string;
  suiteLabel: string | null;
  lockedAt: string;
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

// What the open confirmation acts on: the pair it opened with, and its name, or a
// locked version.
type Confirm =
  | { lock: VersionPair; pair: string }
  | { unlock: LockedVersion }
  | null;

interface LockedVersionCardProps {
  envId: string;
  // Jumps to a workspace tab, for the refusals the author fixes there.
  onGo?: (tab: string) => void;
}

interface UnlockButtonProps {
  disabled?: boolean;
  onClick: () => void;
}

// The JS modules this card uses, typed here for what it passes them.
interface LockMutation<Variables> {
  isPending: boolean;
  mutate: (
    variables: Variables,
    options: { onError: (error: unknown) => void },
  ) => void;
}
const useLockPreview = useLockPreviewJs as unknown as (envId: string) => {
  data?: LockPreview;
};
const useLockVersion = useLockVersionJs as unknown as (
  envId: string,
) => LockMutation<VersionPair>;
const useUnlockVersion = useUnlockVersionJs as unknown as (
  envId: string,
) => LockMutation<{ lockedVersionId: string }>;
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
const ADD_SCENARIOS_FOR = ["lock_too_few_test", "lock_too_few_train"];

const shortDate = (at: string) =>
  new Date(at).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });

export default function LockedVersionCard({
  envId,
  onGo,
}: LockedVersionCardProps) {
  const { data } = useEnvironmentVersions(envId);
  const { data: lock } = useLockPreview(envId);
  const { canEdit, canUnlockVersion } = useSimulationAccess();
  const lockVersion = useLockVersion(envId);
  const unlockVersion = useUnlockVersion(envId);
  const [confirm, setConfirm] = useState<Confirm>(null);
  // Shown once both reads answer, so no state stands in for the other's.
  if (!data || !lock) return null;

  const lockedVersions: LockedVersion[] = data.lockedVersions;
  const { defaults } = data;
  const current = lockedVersions.find(
    (lockedVersion) =>
      !lockedVersion.unlockedAt &&
      lockedVersion.environmentVersionId === lock.environmentVersionId &&
      lockedVersion.suiteVersionId === lock.suiteVersionId,
  );
  const earlier = lockedVersions.filter(
    (lockedVersion) => lockedVersion !== current,
  );
  // The pair the title names: the locked one, else the default a lock would freeze.
  const pair = current
    ? lockPair(current.environmentLabel, current.suiteLabel)
    : lockPair(defaults?.environment?.label, defaults?.suite?.label);
  // A locked card has no badge: its title already says Locked.
  const status = current
    ? null
    : lock.canLock
      ? { label: LOCK_COPY.status.ready, role: "success" }
      : { label: LOCK_COPY.status.blocked, role: "warning" };
  const line = current
    ? LOCK_COPY.lockedLine(
        current.label,
        shortDate(current.lockedAt),
        current.train,
        current.test,
        current.environmentLabel,
      )
    : lock.canLock
      ? LOCK_COPY.readyLine(lock.train, lock.test)
      : lock.refusal?.message;
  const failed = (error: unknown) =>
    enqueueSnackbar(errorMessage(error), { variant: "error" });
  // Locks the pair the dialog was opened on, which a refresh may since have moved.
  const onLock = (pair: VersionPair) => {
    setConfirm(null);
    lockVersion.mutate(pair, { onError: failed });
  };
  // Any locked version can be unlocked, not only the default pair's: an edit moves
  // the default on, and delete still waits for every lock to go.
  const onUnlock = (lockedVersion: LockedVersion) => {
    setConfirm(null);
    unlockVersion.mutate(
      { lockedVersionId: lockedVersion.id },
      { onError: failed },
    );
  };
  const needsScenarios = ADD_SCENARIOS_FOR.includes(lock.refusal?.code ?? "");
  const confirmLock = confirm && "lock" in confirm ? confirm : null;
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
      title={LOCK_COPY.title(pair, Boolean(current))}
      subtitle={current ? LOCK_COPY.subtitleLocked : LOCK_COPY.subtitleOpen}
      action={
        status && (
          <StatusBadge
            status={status.label}
            label={status.label}
            role={status.role}
          />
        )
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
              {LOCK_COPY.head.check}
            </Box>
            <Box component="th" sx={{ ...head, textAlign: "left" }}>
              {LOCK_COPY.head.proves}
            </Box>
            <Box
              component="th"
              sx={{ ...head, textAlign: "right", width: 120 }}
            >
              {LOCK_COPY.head.result}
            </Box>
          </Box>
        </Box>
        <Box component="tbody">
          <Box component="tr" sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Box
              component="td"
              sx={{ ...cell, fontWeight: "fontWeightMedium" }}
            >
              {LOCK_COPY.reference.label}
            </Box>
            <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
              {LOCK_COPY.reference.proves}
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
                ? LOCK_COPY.reference.passed
                : LOCK_COPY.reference.unproved}
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
          canUnlockVersion && (
            <UnlockButton
              disabled={unlockVersion.isPending}
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
                {LOCK_COPY.addScenarios}
              </Button>
            )}
            <ViewerLock locked={!canEdit} title={LOCK_COPY.viewer}>
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
                      !canEdit || !lock.canLock || lockVersion.isPending
                    }
                    onClick={() =>
                      setConfirm({
                        lock: {
                          environmentVersionId: lock.environmentVersionId,
                          suiteVersionId: lock.suiteVersionId,
                        },
                        pair,
                      })
                    }
                    startIcon={
                      <Iconify icon="solar:lock-keyhole-linear" width={14} />
                    }
                    sx={{ flexShrink: 0 }}
                  >
                    {LOCK_COPY.lock}
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
            {LOCK_COPY.earlier}
          </Typography>
          {earlier.map((lockedVersion) => (
            <Stack
              key={lockedVersion.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ minHeight: 28 }}
            >
              <Typography
                sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
              >
                {LOCK_COPY.earlierLine(
                  lockedVersion,
                  shortDate(lockedVersion.lockedAt),
                )}
              </Typography>
              {!lockedVersion.unlockedAt && canUnlockVersion && (
                <UnlockButton
                  disabled={unlockVersion.isPending}
                  onClick={() => setConfirm({ unlock: lockedVersion })}
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
          title={LOCK_COPY.lockTitle(confirmLock.pair)}
          content={LOCK_COPY.lockBody}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => onLock(confirmLock.lock)}
            >
              {LOCK_COPY.lockAction}
            </Button>
          }
        />
      )}
      {confirmUnlock && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={LOCK_COPY.unlockTitle(
            lockPair(confirmUnlock.environmentLabel, confirmUnlock.suiteLabel),
          )}
          content={LOCK_COPY.unlockBody(confirmUnlock.label)}
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => onUnlock(confirmUnlock)}
            >
              {LOCK_COPY.unlockAction}
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
      {LOCK_COPY.unlock}
    </Button>
  );
}
