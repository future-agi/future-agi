import { useState } from "react";
import { useSkillV11Scope } from "src/api/skills/testing";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Alert, Button, MenuItem, Stack, TextField } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { getSkillErrorCode, useCreateSkill, useSkillTemplates } from "src/api/skills";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { paths } from "src/routes/paths";
import { LIMITS, SKILL_NAME_PATTERN } from "src/sections/skills/constants";

function NewSkillDialogContent({ open, onClose, initialTemplate = "blank" }) {
  const navigate = useNavigate();
  const templates = useSkillTemplates();
  const create = useCreateSkill();
  const [created, setCreated] = useState(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [templateId, setTemplateId] = useState(initialTemplate);
  const [nameTaken, setNameTaken] = useState(false);
  const [error, setError] = useState("");
  const validName = name.length > 0 && name.length <= LIMITS.MAX_NAME && SKILL_NAME_PATTERN.test(name);
  const validDescription = description.trim().length > 0 && description.length <= LIMITS.MAX_DESCRIPTION;

  const submit = async () => {
    if (!validName || !validDescription || nameTaken || create.isPending) return;
    setError("");
    try {
      const skill = await create.mutateAsync({ name, description: description.trim(), templateId });
      enqueueSnackbar("Skill created", { variant: "success" });
      setCreated(skill);
    } catch (failure) {
      if (getSkillErrorCode(failure) === "name_taken") setNameTaken(true);
      else setError("Couldn't create this skill. Your inputs are still here. Try again.");
    }
  };

  if (created) return (
    <ModalWrapper open={open} onClose={onClose} title="Skill draft created" subTitle="Nothing is published. Continue writing, or ask for a private AI proposal to review."
      actionBtnTitle="Open editor" isValid onSubmit={() => { onClose(); navigate(paths.dashboard.skills.edit(created.id)); }}>
      <Stack spacing={2}><Alert severity="info">Provide your policy. Missing facts will be questions, not invented rules.</Alert>
        <Button variant="outlined" disabled={created.permissions?.canRunTests !== true} onClick={() => { onClose(); navigate(`${paths.dashboard.skills.edit(created.id)}?assist=draft`); }}>Draft with AI</Button>
      </Stack>
    </ModalWrapper>
  );
  return (
    <ModalWrapper open={open} onClose={onClose} title="New skill" actionBtnTitle="Create skill"
      onSubmit={submit} isValid={validName && validDescription && !nameTaken} isLoading={create.isPending}>
      <Stack spacing={2}>
        {error && <Alert severity="error">{error}</Alert>}
        <TextField autoFocus required label="Name" size="small" value={name} disabled={create.isPending}
          onChange={(event) => { setName(event.target.value); setNameTaken(false); }}
          error={nameTaken || (name.length > 0 && !validName)} inputProps={{ maxLength: LIMITS.MAX_NAME }}
          helperText={nameTaken ? "This name is already taken in this workspace. Choose another name."
            : "Use 1–64 lowercase letters, numbers, and single hyphens between words. Availability is checked when you create."} />
        <TextField required label="Description" size="small" multiline minRows={3} value={description}
          disabled={create.isPending} onChange={(event) => setDescription(event.target.value)}
          inputProps={{ maxLength: LIMITS.MAX_DESCRIPTION }} helperText="Say what it does and when an agent should use it" />
        <TextField select label="Start from" size="small" value={templateId} disabled={create.isPending}
          onChange={(event) => setTemplateId(event.target.value)}>
          <MenuItem value="blank">Blank</MenuItem>
          {(templates.data || []).filter((template) => template.id !== "blank").map((template) => (
            <MenuItem key={template.id} value={template.id}>{template.title}</MenuItem>
          ))}
        </TextField>
        {templates.isError && <Alert severity="warning">Templates could not load. You can still start with a blank skill.</Alert>}
      </Stack>
    </ModalWrapper>
  );
}

NewSkillDialog.propTypes = { open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired, initialTemplate: PropTypes.string };

export default function NewSkillDialog(props) {
  const { identity } = useSkillV11Scope();
  return props.open ? <NewSkillDialogContent key={identity} {...props} /> : null;
}
NewSkillDialogContent.propTypes = NewSkillDialog.propTypes;
