import { Button } from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  environmentVersionsKey,
  unlockVersion,
} from "src/api/simulate-environments/versions";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { DELETE_REFUSAL_COPY } from "../myEnvironments.constants";
import useSimulationAccess from "./useSimulationAccess";

// The locked version a refused delete names, when the refusal is one.
const lockedVersionOf = (error) => {
  const body = error?.response?.data || error;
  return body?.error === "version_locked" && body.locked_version_id
    ? { id: body.locked_version_id, label: body.label }
    : null;
};

// Shows a refused delete. While a version is locked, an admin gets its Unlock in
// the refusal; the unlock works with versioning off, so it is always offered.
export default function useDeleteRefusal() {
  const { canUnlockVersion } = useSimulationAccess();
  const queryClient = useQueryClient();
  const unlock = useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ envId, lockedVersionId }) =>
      unlockVersion(envId, lockedVersionId),
    onSuccess: (data, { envId }) => {
      if (data) queryClient.setQueryData(environmentVersionsKey(envId), data);
    },
  });

  return (envId, error) => {
    const lockedVersion = canUnlockVersion ? lockedVersionOf(error) : null;
    const onUnlock = (key) => {
      closeSnackbar(key);
      unlock.mutate(
        { envId, lockedVersionId: lockedVersion.id },
        {
          onSuccess: () =>
            enqueueSnackbar(DELETE_REFUSAL_COPY.unlocked(lockedVersion.label), {
              variant: "success",
            }),
          onError: (failure) =>
            enqueueSnackbar(errorMessage(failure), { variant: "error" }),
        },
      );
    };
    enqueueSnackbar(errorMessage(error), {
      variant: "error",
      ...(lockedVersion && {
        action: (key) => (
          <Button size="small" color="inherit" onClick={() => onUnlock(key)}>
            {DELETE_REFUSAL_COPY.unlock(lockedVersion.label)}
          </Button>
        ),
      }),
    });
  };
}
