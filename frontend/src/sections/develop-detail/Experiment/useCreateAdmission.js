import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CREATE_ADMISSION_KEY,
  RETRY_UNCHANGED,
  UNCONFIRMED_ADMISSION,
  admissionCode,
  admissionError,
  confirmCreatedExperiment,
  recoveryKey,
  sameSubmission,
  snapshotSubmission,
} from "./createAdmission";

import {
  captureCreateDraft,
  recoveryDraftKey,
  useCreateDraftRecovery,
} from "./createAdmissionDraft";

/** Only a saved, unconfirmed submission may reuse its already-reserved name. */
export function useCreateNameTaken(datasetId, name, unavailable, form) {
  // Local draft state, not an API read. A failed retry must not erase the saved
  // identity; keep it through idle time and drawer remounts in this session.
  const { data: saved } = useQuery({
    queryKey: recoveryKey(datasetId),
    queryFn: () => null,
    enabled: false,
    initialData: null,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  useCreateDraftRecovery(datasetId, saved, form);
  const retrying =
    saved?.dataset_id === datasetId &&
    String(saved?.name ?? "").trim() === String(name ?? "").trim();
  return Boolean(unavailable && !retrying);
}

/** Own the submission boundary, leaving the form's success callbacks intact. */
export function useCreateAdmission(post, options = {}) {
  const client = useQueryClient();
  const unresolved = useRef(null);
  const submittedDraft = useRef(null);
  const inFlight = useRef(null);
  const [settling, setSettling] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const mutation = useMutation({
    ...options,
    mutationKey: CREATE_ADMISSION_KEY,
    retry: false,
    /** @param {{ dataset_id: string }} body */
    mutationFn: async (body) => {
      try {
        return confirmCreatedExperiment(await post(body));
      } catch (error) {
        throw admissionError(error);
      }
    },
    onError: (error, body, context) => {
      if (admissionCode(error) === UNCONFIRMED_ADMISSION) {
        unresolved.current = body;
        client.setQueryData(recoveryKey(body.dataset_id), body);
        if (submittedDraft.current) {
          client.setQueryData(recoveryDraftKey(body.dataset_id), (draft) =>
            draft &&
            sameSubmission(/** @type {{ body: unknown }} */ (draft).body, body)
              ? draft
              : { ...submittedDraft.current, body },
          );
        }
      }
      return options.onError?.(error, body, context);
    },
    onSuccess: (response, body, context) => {
      unresolved.current = null;
      client.setQueryData(recoveryKey(body.dataset_id), (saved) =>
        saved && sameSubmission(saved, body) ? null : saved,
      );
      client.setQueryData(recoveryDraftKey(body.dataset_id), (draft) =>
        draft &&
        sameSubmission(/** @type {{ body: unknown }} */ (draft).body, body)
          ? null
          : draft,
      );
      return options.onSuccess?.(response, body, context);
    },
  });

  const execute = async (body, callbacks) => {
    let snapshot;
    let response;
    let failure = null;
    try {
      snapshot = snapshotSubmission(body);
      const recovery =
        unresolved.current ??
        client.getQueryData(recoveryKey(snapshot.dataset_id));
      if (recovery && !sameSubmission(recovery, snapshot))
        throw new Error(RETRY_UNCHANGED);
      response = await mutation.mutateAsync(snapshot);
      // Await receipt/readback callbacks too: a settled POST is not a settled UI.
      if (mounted.current)
        await callbacks?.onSuccess?.(response, snapshot, undefined);
      return response;
    } catch (error) {
      failure = admissionError(error);
      if (mounted.current)
        await callbacks?.onError?.(failure, snapshot, undefined);
      throw failure;
    } finally {
      try {
        if (mounted.current)
          await callbacks?.onSettled?.(response, failure, snapshot, undefined);
      } finally {
        inFlight.current = null;
        if (mounted.current) setSettling(false);
      }
    }
  };
  const start = (body, callbacks) => {
    // Set the latch synchronously, before either React or React Query renders.
    if (inFlight.current) return inFlight.current;
    const snapshot = snapshotSubmission(body);
    submittedDraft.current = captureCreateDraft(client, snapshot.dataset_id);
    setSettling(true);
    inFlight.current = Promise.resolve().then(() =>
      execute(snapshot, callbacks),
    );
    return inFlight.current;
  };
  return {
    ...mutation,
    isPending: mutation.isPending || settling,
    mutate: (body, callbacks) => {
      void start(body, callbacks).catch(() => {});
    },
    mutateAsync: start,
  };
}
