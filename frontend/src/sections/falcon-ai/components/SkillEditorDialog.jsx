import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import Iconify from "src/components/iconify";
import { createSkill, updateSkill, deleteSkill } from "../hooks/useFalconAPI";
import { captureFalconRequest } from "../store/falconRequestGuard";

// A new skill starts on this icon, and an Icon field left empty saves it too.
// An edited skill starts on its own. The server stores `icon` (Skill.icon),
// and the skill lists, picker and slash menu show it.
const DEFAULT_ICON = "solar:star-linear";

const INITIAL_STATE = {
  name: "",
  description: "",
  icon: DEFAULT_ICON,
  instructions: "",
  trigger_phrases: [],
};

// Same rules and messages as the Customize skill editor (validateSkillForm in
// CustomizePanel.jsx); only this window has the Icon field. Change both together.
function withPendingPhrase(phrases, pending) {
  const phrase = pending.trim();
  return phrase && !phrases.includes(phrase) ? [...phrases, phrase] : phrases;
}

function validateSkillForm(form, phrases) {
  const name = form.name.trim();
  const instructions = form.instructions.trim();
  if (!name) {
    return { field: "name", message: "Name is required" };
  }
  if (name.length < 2) {
    return { field: "name", message: "Name must be at least 2 characters" };
  }
  if (!instructions) {
    return { field: "instructions", message: "Instructions are required" };
  }
  if (instructions.length < 10) {
    return {
      field: "instructions",
      message: "Instructions should be at least 10 characters",
    };
  }
  if (phrases.length === 0) {
    return {
      field: "trigger_phrases",
      message: "Add at least one trigger phrase",
    };
  }
  return null;
}

export default function SkillEditorDialog({ open, skill, onClose, onSaved }) {
  const [form, setForm] = useState(INITIAL_STATE);
  const [phraseInput, setPhraseInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState(null);

  const isEdit = !!skill;

  useEffect(() => {
    if (skill) {
      setForm({
        name: skill.name || "",
        description: skill.description || "",
        icon: skill.icon || DEFAULT_ICON,
        instructions: skill.instructions || "",
        trigger_phrases: skill.trigger_phrases || [],
      });
    } else {
      setForm(INITIAL_STATE);
    }
    setPhraseInput("");
    setError(null);
  }, [skill, open]);

  const handleChange = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const handleAddPhrase = (e) => {
    if (e.key === "Enter" && phraseInput.trim()) {
      e.preventDefault();
      const phrase = phraseInput.trim();
      if (!form.trigger_phrases.includes(phrase)) {
        setForm((prev) => ({
          ...prev,
          trigger_phrases: [...prev.trigger_phrases, phrase],
        }));
      }
      setPhraseInput("");
    }
  };

  const handleRemovePhrase = (phrase) => {
    setForm((prev) => ({
      ...prev,
      trigger_phrases: prev.trigger_phrases.filter((p) => p !== phrase),
    }));
  };

  const handleSave = async () => {
    const phrases = withPendingPhrase(form.trigger_phrases, phraseInput);
    const invalid = validateSkillForm(form, phrases);
    if (invalid) {
      setError(invalid);
      return;
    }

    const isCurrent = captureFalconRequest();
    const payload = {
      ...form,
      icon: form.icon.trim() || DEFAULT_ICON,
      trigger_phrases: phrases,
    };
    setSaving(true);
    setError(null);
    try {
      if (isEdit) {
        await updateSkill(skill.id, payload);
      } else {
        await createSkill(payload);
      }
      if (!isCurrent()) return;
      setPhraseInput("");
      onSaved?.();
      onClose();
    } catch (err) {
      setError({
        field: null,
        message:
          err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          "Failed to save skill.",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!skill?.id) return;
    const isCurrent = captureFalconRequest();
    setDeleting(true);
    try {
      await deleteSkill(skill.id);
      if (!isCurrent()) return;
      onSaved?.();
      onClose();
    } catch {
      setError({ field: null, message: "Failed to delete skill." });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" component="span">
          {isEdit ? "Edit Skill" : "Create Skill"}
        </Typography>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5, pt: 1 }}>
          <TextField
            label="Name"
            required
            value={form.name}
            onChange={handleChange("name")}
            fullWidth
            size="small"
            placeholder="e.g. Build a Dataset"
            error={error?.field === "name"}
          />

          <TextField
            label="Description"
            value={form.description}
            onChange={handleChange("description")}
            fullWidth
            size="small"
            placeholder="What does this skill do?"
          />

          {/* The icon the skill lists, picker and slash menu show, with a
              preview of what was typed. Left empty, it saves the default.
              Icons are bundled, not fetched, so the hint's examples are
              names every skill list always has (backend-icon-consumers.mjs);
              a name the app doesn't ship previews as the missing-icon box. */}
          <TextField
            label="Icon"
            value={form.icon}
            onChange={handleChange("icon")}
            fullWidth
            size="small"
            placeholder={DEFAULT_ICON}
            helperText="Icon name, e.g. mdi:bug-outline or mdi:star. Leave empty for the default star."
            InputProps={{
              startAdornment: (
                <Box sx={{ display: "flex", alignItems: "center", mr: 1 }}>
                  <Iconify
                    icon={form.icon.trim() || DEFAULT_ICON}
                    width={20}
                    sx={{ color: "text.secondary" }}
                  />
                </Box>
              ),
            }}
          />

          <TextField
            label="Instructions"
            required
            value={form.instructions}
            onChange={handleChange("instructions")}
            fullWidth
            multiline
            minRows={4}
            maxRows={12}
            placeholder="Write detailed instructions for how Falcon should behave when this skill is active..."
            error={error?.field === "instructions"}
          />

          {/* Trigger Phrases. Plain words, never "/...": a slash command
              comes from the skill name, not from these phrases. */}
          <Box>
            <TextField
              label="Trigger phrases"
              required
              value={phraseInput}
              onChange={(e) => setPhraseInput(e.target.value)}
              onKeyDown={handleAddPhrase}
              fullWidth
              size="small"
              placeholder="e.g. weekly summary"
              helperText="Press Enter to add each phrase"
              error={error?.field === "trigger_phrases"}
            />
            {form.trigger_phrases.length > 0 && (
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mt: 1 }}>
                {form.trigger_phrases.map((phrase) => (
                  <Chip
                    key={phrase}
                    label={phrase}
                    size="small"
                    onDelete={() => handleRemovePhrase(phrase)}
                    sx={{
                      bgcolor: "background.neutral",
                    }}
                  />
                ))}
              </Box>
            )}
          </Box>

          {error && <Alert severity="error">{error.message}</Alert>}
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
        {isEdit && (
          <Button
            onClick={handleDelete}
            color="error"
            disabled={deleting}
            startIcon={
              deleting ? (
                <CircularProgress size={16} />
              ) : (
                <Iconify icon="solar:trash-bin-trash-linear" width={20} />
              )
            }
            sx={{ mr: "auto" }}
          >
            Delete
          </Button>
        )}
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={saving}
          startIcon={
            saving ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isEdit ? "Save" : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

SkillEditorDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  skill: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func,
};
