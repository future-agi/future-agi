import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import ModalWrapper from "src/components/ModalWrapper";
import {
  useCreateSkill,
  useCreateSkillVersion,
} from "src/api/simulate-skills/skills";
import { readFolder, skillTemplate } from "./skillLabels";

const ENTRY = { skill: "SKILL.md", context: "AGENTS.md" };
const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const startingFiles = (mode, kind, initialFiles) => {
  if (mode === "version") return initialFiles || [];
  return [
    {
      path: ENTRY[kind],
      content: kind === "skill" ? skillTemplate("", "") : "",
    },
  ];
};

export default function SkillEditorDialog({
  open,
  onClose,
  mode,
  skill,
  initialFiles,
  onSaved,
}) {
  const folderInput = useRef(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("skill");
  const [description, setDescription] = useState("");
  const [note, setNote] = useState("");
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState("");
  const [skipped, setSkipped] = useState([]);
  const create = useCreateSkill();
  const addVersion = useCreateSkillVersion(skill?.id);
  const saving = create.isPending || addVersion.isPending;
  const effectiveKind = mode === "version" ? skill?.kind : kind;

  useEffect(() => {
    if (!open) return;
    const first = startingFiles(
      mode,
      mode === "version" ? skill?.kind : "skill",
      initialFiles,
    );
    setName("");
    setKind("skill");
    setDescription("");
    setNote("");
    setFiles(first);
    setSelected(first[0]?.path || "");
    setSkipped([]);
  }, [open, mode, skill?.kind, initialFiles]);

  const current = useMemo(
    () => files.find((file) => file.path === selected) || null,
    [files, selected],
  );

  const changeKind = (next) => {
    setKind(next);
    const first = startingFiles("create", next);
    setFiles(first);
    setSelected(first[0].path);
    setSkipped([]);
  };

  const editContent = (content) =>
    setFiles((all) =>
      all.map((file) => (file.path === selected ? { ...file, content } : file)),
    );

  const pickFolder = async (event) => {
    const picked = await readFolder(event.target.files);
    event.target.value = "";
    if (!picked.files.length && !picked.skipped.length) return;
    setFiles(picked.files);
    setSkipped(picked.skipped);
    setSelected(
      picked.files.find((file) => file.path === ENTRY[effectiveKind])?.path ||
        picked.files[0]?.path ||
        "",
    );
    if (
      mode === "create" &&
      !name &&
      picked.folder &&
      NAME.test(picked.folder)
    ) {
      setName(picked.folder);
    }
  };

  const nameError = mode === "create" && name !== "" && !NAME.test(name);
  const valid =
    files.length > 0 &&
    skipped.length === 0 &&
    (mode === "version" || (name !== "" && !nameError));

  const submit = async () => {
    const body = {
      note,
      files: files.map(({ path, content }) => ({ path, content })),
    };
    try {
      const saved =
        mode === "version"
          ? await addVersion.mutateAsync(body)
          : await create.mutateAsync({
              ...body,
              name,
              kind,
              description: kind === "context" ? description : "",
            });
      enqueueSnackbar(
        mode === "version"
          ? `Saved as v${saved.version_number}`
          : `Created ${saved.name} v1`,
        { variant: "success" },
      );
      onSaved?.(saved);
      onClose();
    } catch (error) {
      enqueueSnackbar(error?.message || "Could not save the skill", {
        variant: "error",
      });
    }
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      onCancelBtn={onClose}
      onSubmit={submit}
      isValid={valid}
      isLoading={saving}
      title={mode === "version" ? `Edit ${skill?.name}` : "New skill"}
      subTitle={
        mode === "version"
          ? "Saving makes the next version. Tags stay where they are until you promote."
          : "A folder with a SKILL.md and any supporting files, or an agent's always-on instructions."
      }
      actionBtnTitle={mode === "version" ? "Save version" : "Create skill"}
      modalWidth="760px"
    >
      <Stack spacing={1.5}>
        {mode === "create" && (
          <Stack direction="row" spacing={1.5}>
            <TextField
              select
              size="small"
              label="Kind"
              value={kind}
              onChange={(event) => changeKind(event.target.value)}
              sx={{ width: 200 }}
            >
              <MenuItem value="skill">Skill</MenuItem>
              <MenuItem value="context">Agent context</MenuItem>
            </TextField>
            <TextField
              size="small"
              label="Name"
              value={name}
              onChange={(event) => setName(event.target.value.trim())}
              error={nameError}
              helperText={
                nameError
                  ? "Lowercase letters, digits and single hyphens"
                  : kind === "skill"
                    ? "Must match the name in SKILL.md"
                    : " "
              }
              fullWidth
            />
          </Stack>
        )}
        {mode === "create" && kind === "context" && (
          <TextField
            size="small"
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            fullWidth
          />
        )}
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            select
            size="small"
            label="File"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            sx={{ minWidth: 240 }}
            disabled={!files.length}
          >
            {files.map((file) => (
              <MenuItem key={file.path} value={file.path}>
                {file.path}
              </MenuItem>
            ))}
          </TextField>
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:folder-linear" width={16} />}
            onClick={() => folderInput.current?.click()}
          >
            Upload a folder
          </Button>
          <input
            ref={folderInput}
            type="file"
            webkitdirectory=""
            multiple
            hidden
            onChange={pickFolder}
          />
        </Stack>
        {skipped.length > 0 && (
          <Typography sx={{ typography: "s3", color: "status.danger.fg" }}>
            {`These files can't be read as text, so nothing will be saved: ${skipped.join(", ")}`}
          </Typography>
        )}
        <TextField
          multiline
          minRows={12}
          maxRows={20}
          value={current?.content ?? ""}
          onChange={(event) => editContent(event.target.value)}
          disabled={!current}
          InputProps={{ sx: { fontFamily: "monospace", typography: "s1" } }}
          fullWidth
        />
        <TextField
          size="small"
          label="Note"
          placeholder="What changed"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          fullWidth
        />
        {mode === "version" && (
          <Box>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`${files.length} file${files.length === 1 ? "" : "s"} in this version`}
            </Typography>
          </Box>
        )}
      </Stack>
    </ModalWrapper>
  );
}

SkillEditorDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  mode: PropTypes.oneOf(["create", "version"]).isRequired,
  skill: PropTypes.object,
  initialFiles: PropTypes.array,
  onSaved: PropTypes.func,
};
