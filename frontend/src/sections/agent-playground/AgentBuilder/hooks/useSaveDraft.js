import { useRef, useCallback, useEffect } from "react";
import {
  useSaveDraftVersion,
  useCreateVersion,
} from "src/api/agent-playground/agent-playground";
import { buildVersionPayload } from "../../utils/versionPayloadUtils";
import {
  useAgentPlaygroundStore,
  useWorkflowRunStore,
  resetWorkflowRunStore,
} from "../../store";
import logger from "src/utils/logger";
import { enqueueSnackbar } from "notistack";
import useCanEditAgent from "../../hooks/useCanEditAgent";

/**
 * Hook that returns `saveDraft`, `ensureDraft`, and `promoteDraft` functions.
 *
 * `saveDraft` — for active versions, creates a new draft via POST with full payload.
 *   For draft versions, this is a no-op (content is persisted via individual CRUD calls).
 * `ensureDraft` — async guard for individual mutations: returns `true` when a
 *   draft version is ready, `"created"` when a new draft was auto-created, or
 *   `false` if creation fails. Transitions are fully optimistic — no confirmation
 *   dialog is shown. The Draft badge appears immediately and the POST fires in
 *   the background.
 * `promoteDraft` — promotes a draft to active via metadata-only PUT (status + commit_message).
 *
 * @param {{ onCreateDraft?: (newVersionData: object) => void }} options
 * @returns {{ saveDraft: () => void, ensureDraft: () => Promise<boolean|"created">, promoteDraft: (options?: object) => void }}
 */
export default function useSaveDraft({ onCreateDraft } = {}) {
  const { mutate: saveDraftMutation } = useSaveDraftVersion();
  const { mutate: createVersionMutation } = useCreateVersion();
  const { isReadOnly } = useCanEditAgent();

  const isCreatingDraftRef = useRef(false);
  const pendingSaveRef = useRef(false);
  const draftCreationPromiseRef = useRef(null);
  const draftResolveRef = useRef(null);
  const debounceTimerRef = useRef(null);
  const firstCallTimeRef = useRef(null);
  const wasDirtyRef = useRef(false);

  const DEBOUNCE_MS = 200;
  const MAX_WAIT_MS = 2000;

  /**
   * Promote a draft version to active (metadata-only PUT).
   * @param {{ commitMessage?: string, onError?: Function }} [options]
   */
  const promoteDraft = useCallback(
    ({ commitMessage, onError: callerOnError } = {}) => {
      if (isReadOnly) return;
      const { currentAgent } = useAgentPlaygroundStore.getState();
      const graphId = currentAgent?.id;
      const versionId = currentAgent?.version_id;

      if (!graphId || !versionId) return;

      saveDraftMutation(
        {
          graphId,
          versionId,
          payload: {
            status: "active",
            ...(commitMessage && { commit_message: commitMessage }),
          },
        },
        { onError: callerOnError },
      );
    },
    [saveDraftMutation, isReadOnly],
  );

  const saveDraft = useCallback(
    ({ onError: callerOnError } = {}) => {
      if (isReadOnly) return;
      const { currentAgent, nodes, edges } = useAgentPlaygroundStore.getState();
      const graphId = currentAgent?.id;
      const versionId = currentAgent?.version_id;
      const isDraft = currentAgent?.is_draft ?? true;

      if (!graphId || !versionId) return;

      // Clear stale execution states — graph has changed, previous run is no longer relevant
      if (!useWorkflowRunStore.getState().isRunning) {
        const store = useAgentPlaygroundStore.getState();
        store.setNodeExecutionStates({});
        store.setEdgeExecutionStates({});
        resetWorkflowRunStore();
      }

      if (isDraft) {
        // Draft: content is persisted via individual CRUD calls (addNode,
        // updateNode, deleteNode, createConnection, deleteConnection).
        // PUT is metadata-only — nothing to send here.
        return;
      }

      // Active version → create a new draft with full payload
      if (isCreatingDraftRef.current) {
        pendingSaveRef.current = true;
        return;
      }
      isCreatingDraftRef.current = true;
      const payload = buildVersionPayload(nodes, edges);
      createVersionMutation(
        { graphId, payload },
        {
          onSuccess: (res) => {
            isCreatingDraftRef.current = false;
            onCreateDraft?.(res.data?.result);
            if (pendingSaveRef.current) {
              pendingSaveRef.current = false;
            }
          },
          onError: () => {
            isCreatingDraftRef.current = false;
            pendingSaveRef.current = false;
            callerOnError?.();
          },
        },
      );
    },
    [createVersionMutation, onCreateDraft, isReadOnly],
  );

  /**
   * Fire the actual draft creation POST. Called when the debounce timer fires.
   * Snapshots the CURRENT store (which includes all rapid edits accumulated
   * during the debounce window), remaps IDs, and POSTs.
   */
  const executeDraftCreation = useCallback(
    (graphId) => {
      debounceTimerRef.current = null;
      firstCallTimeRef.current = null;

      // Snapshot for rollback — this is the CURRENT store, including all
      // optimistic edits from every caller during the debounce window.
      const { nodes, edges, selectedNode } = useAgentPlaygroundStore.getState();
      const snapshot = {
        nodes: [...nodes],
        edges: [...edges],
        selectedNode,
      };

      // Prevent AgentBuilder's useEffect from calling loadVersion
      useAgentPlaygroundStore.setState({ _skipNextLoadVersion: true });

      // Close the drawer before remapping IDs to prevent NodeDrawer's
      // discard dialog from firing on the ID change.
      useAgentPlaygroundStore.getState().clearSelectedNode();

      // Remap all node IDs to fresh UUIDs and apply to store
      const { remappedNodes, remappedEdges } = useAgentPlaygroundStore
        .getState()
        .remapNodeIds();

      // Build API payload from the remapped state and fire POST
      const payload = buildVersionPayload(remappedNodes, remappedEdges);

      createVersionMutation(
        { graphId, payload },
        {
          onSuccess: (res) => {
            const newVersionData = res.data?.result;
            try {
              // Sync backend-assigned edge IDs before onCreateDraft so the
              // store has correct IDs for any subsequent edge operations.
              const nodeConnections =
                newVersionData?.nodeConnections ||
                newVersionData?.node_connections;
              if (nodeConnections) {
                useAgentPlaygroundStore
                  .getState()
                  .syncEdgeIdsFromResponse(nodeConnections);
              }
              onCreateDraft?.(newVersionData);
              wasDirtyRef.current = false;
              draftResolveRef.current?.("created");
            } catch (err) {
              logger.error(
                "[useSaveDraft] onCreateDraft failed after draft creation",
                err,
              );
              useAgentPlaygroundStore.getState().restoreGraphSnapshot(snapshot);
              useAgentPlaygroundStore.setState((state) => ({
                currentAgent: {
                  ...state.currentAgent,
                  is_draft: false,
                  version_status: "active",
                },
                _skipNextLoadVersion: false,
                ...(wasDirtyRef.current && { _isNodeFormDirty: true }),
              }));
              wasDirtyRef.current = false;
              enqueueSnackbar("Failed to create draft", { variant: "error" });
              draftResolveRef.current?.(false);
            } finally {
              useAgentPlaygroundStore.setState({ _isDraftCreating: false });
              draftCreationPromiseRef.current = null;
              draftResolveRef.current = null;
            }
          },
          onError: () => {
            useAgentPlaygroundStore.getState().restoreGraphSnapshot(snapshot);
            useAgentPlaygroundStore.setState((state) => ({
              currentAgent: {
                ...state.currentAgent,
                is_draft: false,
                version_status: "active",
              },
              _skipNextLoadVersion: false,
              _isDraftCreating: false,
              ...(wasDirtyRef.current && { _isNodeFormDirty: true }),
            }));
            wasDirtyRef.current = false;
            enqueueSnackbar("Failed to create draft", { variant: "error" });
            draftResolveRef.current?.(false);
            draftCreationPromiseRef.current = null;
            draftResolveRef.current = null;
          },
        },
      );
    },
    [createVersionMutation, onCreateDraft],
  );

  /**
   * Async guard: ensures a draft version exists before individual mutations.
   * - Already a draft → resolves `true` immediately.
   * - Active version → debounces rapid calls (200ms), then snapshots the
   *   current store (including ALL accumulated edits) and POSTs once.
   * - Returns `"created"` for all callers within the debounce window.
   * - Callers that arrive after the POST fires get `true` (fire own API).
   * - Returns `false` if creation fails.
   *
   * Callers should apply their optimistic edit to the store BEFORE calling
   * ensureDraft(). The edit will be included in the POST payload automatically.
   */
  const ensureDraft = useCallback(
    async ({ skipDirtyCheck = false } = {}) => {
      if (isReadOnly) return false;
      // Debounce window still open — reset timer, return same promise.
      // The caller's optimistic edit is already in the store and will be
      // included when the debounce fires and snapshots the current state.
      if (draftCreationPromiseRef.current && debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        const { currentAgent } = useAgentPlaygroundStore.getState();
        const graphId = currentAgent?.id;
        const elapsed = Date.now() - (firstCallTimeRef.current || 0);
        const delay = elapsed >= MAX_WAIT_MS ? 0 : DEBOUNCE_MS;
        debounceTimerRef.current = setTimeout(
          () => executeDraftCreation(graphId),
          delay,
        );
        return draftCreationPromiseRef.current;
      }

      // POST already in-flight (debounce fired) — wait for it.
      // Return `true` so the caller fires its own individual API call.
      if (draftCreationPromiseRef.current) {
        const result = await draftCreationPromiseRef.current;
        return result === "created" ? true : result;
      }

      const { currentAgent, _isNodeFormDirty } =
        useAgentPlaygroundStore.getState();
      if (currentAgent?.is_draft) return true;

      const graphId = currentAgent?.id;
      if (!graphId) return false;

      // If a NodeDrawer form has unsaved changes, ask the user to discard first.
      // This prevents creating a draft that silently loses their in-progress edits.
      // Form submissions pass skipDirtyCheck=true — clear the flag since data is being saved.
      // Track previous value so it can be restored if draft creation fails.
      if (skipDirtyCheck && _isNodeFormDirty) {
        wasDirtyRef.current = true;
        useAgentPlaygroundStore.setState({ _isNodeFormDirty: false });
      }
      if (!skipDirtyCheck && _isNodeFormDirty) {
        const shouldProceed = await new Promise((resolve) => {
          useAgentPlaygroundStore.getState().openDraftConfirmDialog(
            () => {
              // Close the drawer to discard the dirty form — prevents NodeDrawer's
              // own discard dialog from firing when remapNodeIds() changes IDs.
              useAgentPlaygroundStore.getState().clearSelectedNode();
              resolve(true);
            },
            "You have unsaved changes. They will be discarded when creating a draft. Continue?",
            () => resolve(false),
          );
        });
        if (!shouldProceed) return false;
      }

      // Optimistically show Draft badge immediately and block tab navigation
      useAgentPlaygroundStore.setState((state) => ({
        currentAgent: {
          ...state.currentAgent,
          is_draft: true,
          version_status: "draft",
        },
        _isDraftCreating: true,
      }));

      // Create deferred promise — resolved when the POST completes
      const promise = new Promise((resolve) => {
        draftResolveRef.current = resolve;
      });
      draftCreationPromiseRef.current = promise;
      firstCallTimeRef.current = Date.now();

      // Start debounce timer — delays the POST to batch rapid edits
      debounceTimerRef.current = setTimeout(
        () => executeDraftCreation(graphId),
        DEBOUNCE_MS,
      );

      return promise;
    },
    [executeDraftCreation, isReadOnly],
  );

  // Clean up debounce timer on unmount to prevent firing against unmounted component
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
        // Revert optimistic is_draft — POST will never fire
        useAgentPlaygroundStore.setState((state) => ({
          currentAgent: {
            ...state.currentAgent,
            is_draft: false,
            version_status: "active",
          },
          _isDraftCreating: false,
        }));
        if (wasDirtyRef.current) {
          useAgentPlaygroundStore.setState({ _isNodeFormDirty: true });
          wasDirtyRef.current = false;
        }
      }
      draftResolveRef.current?.(false);
      draftCreationPromiseRef.current = null;
      draftResolveRef.current = null;
    };
  }, []);

  return { saveDraft, ensureDraft, promoteDraft };
}
