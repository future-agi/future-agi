import React from "react";
import PropTypes from "prop-types";
import { Alert } from "@mui/material";
import { useMutationState, useQuery } from "@tanstack/react-query";
import { CREATE_ADMISSION_KEY, recoveryKey } from "./createAdmission";
import { isSurfaceShown } from "src/launch/launchSwitch";

/** Keep submission errors inside the modal instead of in its inert backdrop. */
export default function CreateAdmissionNotice({ datasetId }) {
  const submissions = useMutationState({
    filters: {
      mutationKey: CREATE_ADMISSION_KEY,
      predicate: (mutation) =>
        /** @type {{ dataset_id?: string }} */ (mutation.state.variables)
          ?.dataset_id === datasetId,
    },
    select: (mutation) => ({
      id: mutation.mutationId,
      status: mutation.state.status,
      error: mutation.state.error,
    }),
  });
  const { data: saved } = useQuery({
    queryKey: recoveryKey(datasetId),
    queryFn: () => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const latest = submissions.reduce(
    (last, item) => (!last || item.id > last.id ? item : last),
    null,
  );
  const message = latest?.status === "error" ? latest.error?.message : null;
  // J09: durable recovery remains intact; its NEW presentation stays opt-in.
  if (!isSurfaceShown("exp.new") || (!message && !saved)) return null;
  return (
    <Alert severity="error">
      {message ||
        "Execution admission is unconfirmed. Retry the saved configuration unchanged to confirm execution."}
    </Alert>
  );
}
CreateAdmissionNotice.propTypes = { datasetId: PropTypes.string };
