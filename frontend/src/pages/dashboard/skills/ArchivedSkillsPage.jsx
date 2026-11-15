import { Helmet } from "react-helmet-async";
import { Button, Stack } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import PageHeader from "src/components/page-header";
import CapabilityNotice from "src/sections/skills/components/CapabilityNotice";
import { paths } from "src/routes/paths";
import { useContextAuthoring } from "src/sections/skills/contexts/ContextLibraryView";
import ArchivedSkillsView from "src/sections/skills/library/ArchivedSkillsView";

export default function ArchivedSkillsPage() {
  const composition = useContextAuthoring();
  return <><Helmet><title>Archived skills</title></Helmet>{composition?.bindings.archived ? <ArchivedSkillsView {...composition.bindings.archived} scopeKey={composition.scopeKey} /> : <Stack spacing={2} sx={{ p: { xs: 2, md: 3 } }}><PageHeader title="Archived skills" /><CapabilityNotice title="Archived skills can't be browsed in this workspace yet" description="Archived skills are kept, but restoring one isn't available in this workspace. Contact support to restore it." actions={<Button component={RouterLink} to={paths.dashboard.skills.root} variant="outlined">Back to skills</Button>} /></Stack>}</>;
}
