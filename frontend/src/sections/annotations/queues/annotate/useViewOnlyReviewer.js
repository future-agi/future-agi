import { useAuthContext } from "src/auth/hooks";

/** True for a workspace viewer: they inspect review items, members score (W4-3). */
export function useViewOnlyReviewer() {
  const { role } = useAuthContext();
  return role === "workspace_viewer";
}
