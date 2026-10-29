import { useOutletContext, useParams } from "react-router-dom";

import FixRunPage from "./FixRunPage";

// The fix page. On the improvement route the workspace early-returns its
// Outlet, so this renders as its own full page, like a run does.
export default function WorkspaceImprovementDetail() {
  const { env } = useOutletContext() || {};
  const { fixId } = useParams();
  if (!env) return null;
  return <FixRunPage key={fixId} envId={env.id} fixId={fixId} />;
}
