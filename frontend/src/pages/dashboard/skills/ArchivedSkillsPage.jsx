import { Helmet } from "react-helmet-async";
import { Button, Stack } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import PageHeader from "src/components/page-header";
import { archivedSkillsReady } from "src/api/skills";
import CapabilityNotice from "src/sections/skills/components/CapabilityNotice";
import { paths } from "src/routes/paths";
import ArchivedSkillsController from "src/sections/skills/library/ArchivedSkillsController";

export default function ArchivedSkillsPage() {
  return <><Helmet><title>Archived skills</title></Helmet>{archivedSkillsReady() ? <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}><ArchivedSkillsController /></Stack> : <Stack spacing={2} sx={{ p: { xs: 2, md: 3 } }}><PageHeader title="Archived skills" /><CapabilityNotice title="Archived skills aren't available in this workspace yet" description="Archived skills are kept. An admin can restore them once this is turned on." actions={<Button component={RouterLink} to={paths.dashboard.skills.root} variant="outlined">Back to skills</Button>} /></Stack>}</>;
}
