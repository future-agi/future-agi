import { useState, type ReactElement } from "react";
import { Button } from "@mui/material";
import { closeSnackbar, enqueueSnackbar, type SnackbarKey } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  environmentVersionsKey,
  lockPreviewKey,
  unlockVersion as unlockVersionJs,
} from "src/api/simulate-environments/versions";
import type { HarnessVersionsApi } from "src/generated/api-contracts/api.schemas";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { DELETE_REFUSAL_COPY } from "../myEnvironments.constants";
import { lockPair } from "../workspace/overview/overview.constants";
import UnlockConfirm, {
  type UnlockTarget,
} from "../workspace/overview/UnlockConfirm";
import useSimulationAccess from "./useSimulationAccess";

// The lock a refused delete names, with the pair its confirmation names.
interface LockedVersionRef extends UnlockTarget {
  id: string;
}

// The unlock waiting for its confirmation, and the environment it is for.
interface PendingUnlock {
  envId: string;
  target: LockedVersionRef;
}

interface DeleteRefusal {
  showDeleteRefusal: (envId: string, error: unknown) => void;
  // The unlock's confirmation, rendered by the caller while one is open.
  unlockConfirm: ReactElement | null;
}

// The lock's label rides along, so the outcome can name it.
interface UnlockArgs {
  envId: string;
  target: LockedVersionRef;
}

const unlockVersion = unlockVersionJs as unknown as (
  envId: string,
  lockedVersionId: string,
) => Promise<HarnessVersionsApi | null>;

// The locked version a refused delete names, when the refusal is one. Both its id
// and its label are optional in the contract, so the action needs both.
const lockedVersionOf = (error: unknown): LockedVersionRef | null => {
  const failure = error as { response?: { data?: unknown } } | null;
  const body = (failure?.response?.data ?? error) as {
    error?: string;
    locked_version_id?: string | null;
    label?: string | null;
    environment_label?: string | null;
    suite_label?: string | null;
  } | null;
  return body?.error === "version_locked" &&
    body.locked_version_id &&
    body.label
    ? {
        id: body.locked_version_id,
        label: body.label,
        environmentLabel: body.environment_label ?? null,
        suiteLabel: body.suite_label ?? null,
      }
    : null;
};

// Shows a refused delete. While a version is locked, an admin gets its Unlock in
// the refusal; the unlock works with versioning off, so it is always offered. It
// opens the card's confirmation and sends only once that is confirmed.
export default function useDeleteRefusal(): DeleteRefusal {
  const { canUnlockVersion } = useSimulationAccess() as {
    canUnlockVersion: boolean;
  };
  const queryClient = useQueryClient();
  // The outcome is shown here, not in the mutate call's own callbacks: those
  // never run once the list that refused the delete has unmounted.
  const unlock = useMutation<HarnessVersionsApi | null, unknown, UnlockArgs>({
    meta: { errorHandled: true },
    mutationFn: ({ envId, target }) => unlockVersion(envId, target.id),
    onSuccess: (data, { envId, target }) => {
      if (data) queryClient.setQueryData(environmentVersionsKey(envId), data);
      queryClient.invalidateQueries({ queryKey: lockPreviewKey(envId) });
      // The answer's history names the pair the lock covered.
      const unlocked = data?.locked_versions.find(
        (one) => one.id === target.id,
      );
      const pair = unlocked
        ? lockPair(unlocked.environment_label, unlocked.suite_label)
        : null;
      enqueueSnackbar(DELETE_REFUSAL_COPY.unlocked(target.label, pair), {
        variant: "success",
      });
    },
    onError: (failure) =>
      enqueueSnackbar(errorMessage(failure), { variant: "error" }),
  });

  const [pending, setPending] = useState<PendingUnlock | null>(null);
  const unlockConfirm = pending && (
    <UnlockConfirm
      target={pending.target}
      onClose={() => setPending(null)}
      onConfirm={() => {
        setPending(null);
        unlock.mutate(pending);
      }}
    />
  );

  const showDeleteRefusal = (envId: string, error: unknown) => {
    const lockedVersion = canUnlockVersion ? lockedVersionOf(error) : null;
    const onUnlock = (key: SnackbarKey, target: LockedVersionRef) => {
      closeSnackbar(key);
      setPending({ envId, target });
    };
    enqueueSnackbar(errorMessage(error), {
      variant: "error",
      ...(lockedVersion && {
        action: (key: SnackbarKey) => (
          <Button
            size="small"
            color="inherit"
            onClick={() => onUnlock(key, lockedVersion)}
          >
            {DELETE_REFUSAL_COPY.unlock}
          </Button>
        ),
      }),
    });
  };

  return { showDeleteRefusal, unlockConfirm };
}
