// Rich (attachment-reference) paste is only honoured within the same tab, the
// same signed-in user and the same organization. AuthContext has no workspace
// id, so `workspaceId` is null and the organization is the boundary.
// Provenance comes from app state, never from clipboard bytes.
import { useCallback, useEffect, useRef } from "react";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { TAB_ID } from "./constants";
import { clear } from "./internalClipboardStore";

export function useClipboardProvenance() {
  const { user } = useSafeAuthContext();
  const userId = user?.id ?? null;
  const orgId = user?.organization?.id ?? null;
  const workspaceId = null;

  const ref = useRef({ tabId: TAB_ID, userId, orgId, workspaceId });

  useEffect(() => {
    const prev = ref.current;
    if (prev.userId !== userId || prev.orgId !== orgId) {
      // logout, user change or organization switch: a stale record must not
      // outlive the context it was copied in, even if no paste happens.
      clear();
    }
    ref.current = { tabId: TAB_ID, userId, orgId, workspaceId };
  }, [userId, orgId]);

  return useCallback(() => ref.current, []);
}
