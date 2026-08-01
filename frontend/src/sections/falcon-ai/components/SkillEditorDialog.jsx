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
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { createSkill, updateSkill, deleteSkill } from "../hooks/useFalconAPI";

const INITIAL_STATE = {
  name: "",
  description: "",
  icon: "mdi:star-outline",
  instructions: "",
  trigger_phrases: [],
};

export default function SkillEditorDialog({ open, skill, onClose, onSaved }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
        icon: skill.icon || "mdi:star-outline",
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
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    if (form.name.trim().length < 2) {
      setError("Name must be at least 2 characters");
      return;
    }
    if (!form.instructions.trim()) {
      setError("Instructions are required — tell Falcon how to behave");
      return;
    }
    if (form.instructions.trim().length < 10) {
      setError("Instructions should be at least 10 characters");
      return;
    }

    const pending = phraseInput.trim();
    const phrases =
      pending && !form.trigger_phrases.includes(pending)
        ? [...form.trigger_phrases, pending]
        : form.trigger_phrases;

    if (phrases.length === 0) {
      setError("Add at least one trigger phrase (type and press Enter)");
      return;
    }

    const payload = { ...form, trigger_phrases: phrases };
    setSaving(true);
    setError(null);
    try {
      if (isEdit) {
        await updateSkill(skill.id, payload);
      } else {
        await createSkill(payload);
      }
      setPhraseInput("");
      onSaved?.();
      onClose();
    } catch (err) {
      setError(
        err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          "Failed to save skill.",
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!skill?.id) return;
    setDeleting(true);
    try {
      await deleteSkill(skill.id);
      onSaved?.();
      onClose();
    } catch {
      setError("Failed to delete skill.");
    } finally {
      setDeleting(false);
    }
  };

  const hasPhrase = form.trigger_phrases.length > 0 || !!phraseInput.trim();
  const canSave = form.name.trim() && form.instructions.trim() && hasPhrase;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" component="span">
          {isEdit ? "Edit Skill" : "Create Skill"}
        </Typography>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5, pt: 1 }}>
          <TextField
            label="Name *"
            value={form.name}
            onChange={handleChange("name")}
            fullWidth
            size="small"
            placeholder="e.g. Bug Analyzer"
            error={error && !form.name.trim()}
            helperText={error && !form.name.trim() ? "Name is required" : ""}
          />

          <TextField
            label="Description"
            value={form.description}
            onChange={handleChange("description")}
            fullWidth
            size="small"
            placeholder="Short description shown in skill picker"
          />

          <Box>
            <TextField
              label="Icon"
              value={form.icon}
              onChange={handleChange("icon")}
              fullWidth
              size="small"
              placeholder="mdi:star-outline"
              InputProps={{
                startAdornment: (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      mr: 1,
                    }}
                  >
                    <Iconify
                      icon={form.icon || "mdi:star-outline"}
                      width={20}
                      sx={{ color: "text.secondary" }}
                    />
                  </Box>
                ),
              }}
            />
            <Typography
              variant="caption"
              sx={{ color: "text.disabled", mt: 0.5, display: "block" }}
            >
              Use an MDI icon name (e.g. mdi:bug, mdi:chart-line)
            </Typography>
          </Box>

          <TextField
            label="Instructions *"
            value={form.instructions}
            onChange={handleChange("instructions")}
            fullWidth
            multiline
            minRows={4}
            maxRows={12}
            placeholder="Write the skill prompt here. This tells the AI how to behave when this skill is active..."
            error={error && !form.instructions.trim()}
            helperText={
              error && !form.instructions.trim()
                ? "Instructions are required"
                : "Describe the workflow, reasoning, and approach — not rigid commands"
            }
          />

          {/* Trigger Phrases */}
          <Box>
            <TextField
              label="Trigger Phrases *"
              value={phraseInput}
              onChange={(e) => setPhraseInput(e.target.value)}
              onKeyDown={handleAddPhrase}
              fullWidth
              size="small"
              placeholder="Type a phrase and press Enter"
              helperText="Press Enter to add each phrase — at least one required"
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
                      fontSize: 12,
                      height: 26,
                      bgcolor: isDark
                        ? alpha(theme.palette.common.white, 0.06)
                        : alpha(theme.palette.common.black, 0.05),
                    }}
                  />
                ))}
              </Box>
            )}
          </Box>

          {error && <Alert severity="error">{error}</Alert>}
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
                <Iconify icon="mdi:delete-outline" width={18} />
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
          disabled={!canSave || saving}
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
