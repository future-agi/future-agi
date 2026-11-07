import { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Button, IconButton, ListItemIcon, Menu, MenuItem } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { downloadSkillsZip, useArchiveSkill } from "src/api/skills";
import { HOST_API } from "src/config-global";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { INSTALL_CLIENTS } from "src/sections/skills/constants";
import { buildInstallCommand } from "src/sections/skills/utils/install";

export default function SkillRowMenu({ skill }) {
  const navigate = useNavigate();
  const archive = useArchiveSkill();
  const [anchor, setAnchor] = useState(null);
  const [confirm, setConfirm] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const hasProduction = skill.labels.some((label) => label.name === "production");
  const copyCommand = async () => {
    setAnchor(null);
    try {
      await navigator.clipboard.writeText(buildInstallCommand({ hostApi: HOST_API, names: [skill.name], label: "production", dir: INSTALL_CLIENTS[0].projectDir }));
      enqueueSnackbar("Install command copied", { variant: "success" });
    } catch (_failure) { enqueueSnackbar("Couldn't copy the command. Open the skill's Use tab to copy it.", { variant: "error" }); }
  };
  const download = async () => {
    setAnchor(null);
    setDownloading(true);
    try { await downloadSkillsZip({ names: [skill.name], version: skill.latestVersion }); }
    catch (_failure) { enqueueSnackbar("Couldn't download this skill. Try again.", { variant: "error" }); }
    finally { setDownloading(false); }
  };
  const archiveSkill = async () => {
    if (archive.isPending) return;
    try {
      await archive.mutateAsync(skill.id);
      setConfirm(false);
      enqueueSnackbar("Skill archived", { variant: "success" });
    } catch (_failure) { enqueueSnackbar("Couldn't archive this skill. Try again.", { variant: "error" }); }
  };
  return (
    <Box onClick={(event) => event.stopPropagation()}>
      <IconButton size="small" aria-label={`Actions for ${skill.name}`} aria-haspopup="menu" aria-expanded={Boolean(anchor)}
        onClick={(event) => { event.stopPropagation(); setAnchor(event.currentTarget); }}>
        <Iconify icon="solar:menu-dots-bold" />
      </IconButton>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)} onClick={(event) => event.stopPropagation()}>
        <MenuItem onClick={() => { setAnchor(null); navigate(paths.dashboard.skills.detail(skill.id)); }}><ListItemIcon><Iconify icon="solar:arrow-right-up-linear" width={16} /></ListItemIcon>Open</MenuItem>
        <MenuItem onClick={() => { setAnchor(null); navigate(paths.dashboard.skills.edit(skill.id)); }}><ListItemIcon><Iconify icon="solar:pen-linear" width={16} /></ListItemIcon>Edit</MenuItem>
        <MenuItem disabled={!hasProduction} onClick={copyCommand}><ListItemIcon><Iconify icon="solar:copy-linear" width={16} /></ListItemIcon>Copy install command</MenuItem>
        <MenuItem disabled={!skill.latestVersion || downloading} onClick={download}><ListItemIcon><Iconify icon="solar:download-linear" width={16} /></ListItemIcon>Download</MenuItem>
        <MenuItem onClick={() => { setAnchor(null); setConfirm(true); }}><ListItemIcon><Iconify icon="solar:archive-linear" width={16} /></ListItemIcon>Archive</MenuItem>
      </Menu>
      <ConfirmDialog open={confirm} onClose={() => { if (!archive.isPending) setConfirm(false); }} closeDisabled={archive.isPending}
        title={`Archive ${skill.name}?`} content="This hides the skill from your workspace. Agents will no longer be able to fetch it, including by a label."
        action={<Button variant="contained" color="error" disabled={archive.isPending} onClick={(event) => { event.stopPropagation(); archiveSkill(); }}>Archive</Button>} />
    </Box>
  );
}

SkillRowMenu.propTypes = { skill: PropTypes.object.isRequired };
