import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useArchivedSkills, useRestoreArchivedSkill, useRestoreReviewLoader } from "src/api/skills";
import { useSkillV11Scope } from "src/api/skills/testing";
import { paths } from "src/routes/paths";
import { useSkillsAccess } from "../utils/access";
import ArchivedSkillsView from "./ArchivedSkillsView";

// Wires the Archived skills list to the admin-only endpoints. Rendered only while archivedSkillsReady() is true.
// The scope identity (organization, workspace, person) keys the view, so a switch drops any open review.
const PAGE_SIZE = 25;
export default function ArchivedSkillsController() {
  const navigate = useNavigate();
  const { identity } = useSkillV11Scope();
  const { canAdmin } = useSkillsAccess();
  const [page, setPage] = useState(1);
  const query = useArchivedSkills({ page, pageSize: PAGE_SIZE });
  const loadReview = useRestoreReviewLoader();
  const restore = useRestoreArchivedSkill();
  return <ArchivedSkillsView
    scopeKey={identity} query={query} page={page} pageSize={PAGE_SIZE} onPageChange={setPage} canRestore={canAdmin}
    onReview={(skill) => loadReview(skill.id)}
    onRestore={(skill, input, options) => restore.mutateAsync({ id: skill.id, ...input, idempotencyKey: options.idempotencyKey })}
    onOpenSkill={(skill) => navigate(paths.dashboard.skills.detail(skill.id), { state: { skillName: skill.name } })}
    onBack={() => navigate(paths.dashboard.skills.root)} />;
}
