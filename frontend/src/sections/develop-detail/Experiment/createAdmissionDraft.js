import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  recoveryKey,
  sameSubmission,
  snapshotSubmission,
} from "./createAdmission";
import { EXPERIMENT_STEP } from "./constants";

// Readers exist only while their drawer is mounted. Saved values belong to the
// query client's session, not browser storage, and are paired with a wire body.
const readers = new WeakMap();
let nextMount = 0;
export const recoveryDraftKey = (datasetId) => [
  "experiment-create-recovery-draft",
  datasetId,
];

export function captureCreateDraft(client, datasetId) {
  const reader = readers.get(client)?.get(datasetId);
  return reader
    ? { mount: reader.mount, values: snapshotSubmission(reader.getValues()) }
    : null;
}

const hasRestorableDraft = (client, datasetId) => {
  const saved = client.getQueryData(recoveryKey(datasetId));
  const draft = client.getQueryData(recoveryDraftKey(datasetId));
  return Boolean(saved && draft && sameSubmission(saved, draft.body));
};

/**
 * Whether this drawer opened on a saved, unconfirmed submission, decided once
 * when it mounts. A draft saved while the drawer is already open (its request
 * failed after the drawer was closed and opened again) is restored on the next
 * open, never swapped in over what this drawer already shows or preselects.
 */
function useRestoresAtMount(datasetId, isEditing) {
  const client = useQueryClient();
  const [restores] = useState(
    () => !isEditing && hasRestorableDraft(client, datasetId),
  );
  return restores;
}

/**
 * A drawer that restores a saved request ignores the link that opened it
 * (rows, split, version, Start from), so nothing preselects over the request,
 * now or when the link's rows finish loading.
 */
export function useRecoveryPrefill(datasetId, prefill, isEditing = false) {
  return useRestoresAtMount(datasetId, isEditing) ? null : prefill;
}

export function useCreateDraftRecovery(datasetId, saved, form = {}) {
  const client = useQueryClient();
  const {
    getValues,
    setValue,
    setCurrentStep,
    keepChoices,
    isEditing = false,
  } = form;
  const restores = useRestoresAtMount(datasetId, isEditing);
  const mount = useRef(null);
  if (mount.current === null) mount.current = ++nextMount;
  const restored = useRef(null);
  const { data: draft } = useQuery({
    queryKey: recoveryDraftKey(datasetId),
    queryFn: () => null,
    initialData: null,
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });

  useEffect(() => {
    if (isEditing || !getValues) return undefined;
    if (!readers.has(client)) readers.set(client, new Map());
    const forms = readers.get(client);
    const reader = { mount: mount.current, getValues };
    forms.set(datasetId, reader);
    return () => {
      if (forms.get(datasetId) === reader) forms.delete(datasetId);
    };
  }, [client, datasetId, getValues, isEditing]);

  useEffect(() => {
    if (
      !restores ||
      !setValue ||
      !draft ||
      !saved ||
      draft.mount === mount.current ||
      // Once per drawer: a draft saved later never replaces one being edited.
      restored.current !== null ||
      !sameSubmission(saved, draft.body)
    )
      return;
    restored.current = draft;
    // Never put wire-transformed values back into React Hook Form. Restore the
    // form snapshot taken at submit, not edits made after the failed request.
    const values = snapshotSubmission(draft.values);
    // A field the saved form did not have keeps no fresh default either.
    Object.keys(getValues?.() ?? {})
      .filter((key) => !(key in values))
      .forEach((key) => setValue(key, undefined, { shouldDirty: true }));
    Object.entries(values).forEach(([key, value]) => {
      setValue(key, value, { shouldDirty: true });
    });
    // The saved request's choices win over a fresh drawer's one-time defaults
    // (an empty reference column, evaluations edited after a copy).
    keepChoices?.(values);
    setCurrentStep?.(EXPERIMENT_STEP.REVIEW);
  }, [
    restores,
    draft,
    saved,
    getValues,
    setValue,
    setCurrentStep,
    keepChoices,
  ]);
}
