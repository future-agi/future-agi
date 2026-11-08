import { Helmet } from "react-helmet-async";
import ErrorState from "src/components/error-state";
import { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";
import ArchivedSkillsView from "src/sections/skills/library/ArchivedSkillsView";

export default function ArchivedSkillsPage() {
  const composition = useContextAuthoring();
  return <><Helmet><title>Archived skills</title></Helmet>{composition?.bindings.archived ? <ArchivedSkillsView {...composition.bindings.archived} scopeKey={composition.scopeKey} /> : <ErrorState title="Archived skills unavailable" detail="Current administrator access and the original-identity recovery service are required." />}</>;
}
