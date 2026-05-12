import React, { useEffect, useState, useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import ListItemIcon from "@mui/material/ListItemIcon";
import Divider from "@mui/material/Divider";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  listSkills,
  fetchConnectors,
  deleteConnector,
  updateConnectorTools,
  discoverConnectorTools,
} from "../hooks/useFalconAPI";
import useFalconStore from "../store/useFalconStore";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const CONNECTOR_CATEGORIES = {
  Web: ["github", "gitlab", "jira", "confluence", "slack", "notion", "linear"],
  Desktop: ["vscode", "cursor", "terminal"],
  Data: ["postgres", "mysql", "bigquery", "snowflake", "redis"],
  Cloud: ["aws", "gcp", "azure", "vercel"],
  Custom: [],
};

function categorizeConnector(connector) {
  const name = (connector.name || connector.type || "").toLowerCase();
  for (const [category, keywords] of Object.entries(CONNECTOR_CATEGORIES)) {
    if (category === "Custom") continue;
    if (keywords.some((kw) => name.includes(kw))) return category;
  }
  return "Custom";
}

function getConnectorIcon(connector) {
  if (connector.icon) return connector.icon;
  const name = (connector.name || connector.type || "").toLowerCase();
  if (name.includes("github")) return "mdi:github";
  if (name.includes("gitlab")) return "mdi:gitlab";
  if (name.includes("jira")) return "mdi:jira";
  if (name.includes("slack")) return "mdi:slack";
  if (name.includes("notion")) return "simple-icons:notion";
  if (name.includes("postgres")) return "mdi:database";
  if (name.includes("mysql")) return "mdi:database-outline";
  if (name.includes("redis")) return "mdi:database-arrow-right";
  if (name.includes("aws")) return "mdi:aws";
  if (name.includes("gcp")) return "mdi:google-cloud";
  if (name.includes("azure")) return "mdi:microsoft-azure";
  if (name.includes("vscode")) return "mdi:microsoft-visual-studio-code";
  return "mdi:puzzle-outline";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Panel1NavItem({ icon, label, active, onClick }) {
  const theme = useTheme();
  return (
    <ListItemButton
      selected={active}
      onClick={onClick}
      sx={{
        borderRadius: "8px",
        mb: "2px",
        px: 1.5,
        py: "7px",
        minHeight: 0,
        color: active ? "text.primary" : "text.secondary",
        bgcolor: active ? theme.palette.action.selected : "transparent",
        "&:hover": {
          bgcolor: theme.palette.action.hover,
        },
        "&.Mui-selected": {
          bgcolor: theme.palette.action.selected,
        },
      }}
    >
      <ListItemIcon sx={{ minWidth: 28, color: "inherit" }}>
        <Iconify icon={icon} width={18} />
      </ListItemIcon>
      <ListItemText
        primary={label}
        primaryTypographyProps={{
          fontSize: 13,
          fontWeight: active ? 600 : 500,
        }}
      />
    </ListItemButton>
  );
}

Panel1NavItem.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Panel 1: Navigation sidebar
// ---------------------------------------------------------------------------
function NavigationPanel({ selectedTab, onTabChange }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Box
      sx={{
        width: 200,
        minWidth: 200,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: (t) => `solid 1px ${t.palette.divider}`,
        bgcolor: isDark
          ? alpha(theme.palette.primary.main, 0.04)
          : alpha(theme.palette.primary.main, 0.02),
        height: "100%",
      }}
    >
      {/* Header with back arrow */}
      <Box
        sx={{
          px: 1.5,
          pt: 2,
          pb: 1,
          display: "flex",
          alignItems: "center",
          gap: 0.5,
        }}
      >
        <IconButton
          size="small"
          onClick={() => useFalconStore.getState().setShowCustomize(false)}
          sx={{ width: 24, height: 24, color: "text.secondary" }}
        >
          <Iconify icon="mdi:arrow-left" width={18} />
        </IconButton>
        <Typography
          sx={{
            fontSize: 14,
            fontWeight: 700,
            color: "text.primary",
          }}
        >
          Customize
        </Typography>
      </Box>

      {/* Nav items */}
      <Box sx={{ px: 0.75, flex: 1 }}>
        <Panel1NavItem
          icon="mdi:lightning-bolt"
          label="Skills"
          active={selectedTab === "skills"}
          onClick={() => onTabChange("skills")}
        />
        <Panel1NavItem
          icon="mdi:puzzle-outline"
          label="Connectors"
          active={selectedTab === "connectors"}
          onClick={() => onTabChange("connectors")}
        />
      </Box>

      {/* Bottom CTA */}
      <Box
        sx={{
          px: 1.5,
          pb: 2,
          pt: 1,
        }}
      >
        <Typography
          sx={{
            fontSize: 11,
            color: "text.disabled",
            lineHeight: 1.4,
          }}
        >
          Give Falcon role-level expertise
        </Typography>
      </Box>
    </Box>
  );
}

NavigationPanel.propTypes = {
  selectedTab: PropTypes.string.isRequired,
  onTabChange: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Panel 2: Skills list
// ---------------------------------------------------------------------------
function SkillsListPanel({
  skills,
  selectedId,
  onSelect,
  onCreateClick,
  loading,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return skills;
    const q = search.toLowerCase();
    return skills.filter(
      (s) =>
        s.name?.toLowerCase().includes(q) ||
        s.description?.toLowerCase().includes(q),
    );
  }, [skills, search]);

  return (
    <Box
      sx={{
        width: 250,
        minWidth: 250,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: (t) => `solid 1px ${t.palette.divider}`,
        height: "100%",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          px: 1.5,
          pt: 2,
          pb: 0.5,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography
          sx={{ fontSize: 13, fontWeight: 700, color: "text.primary" }}
        >
          Skills
        </Typography>
        <Box sx={{ display: "flex", gap: 0.25 }}>
          <IconButton
            size="small"
            onClick={onCreateClick}
            title="Create skill"
            sx={{ width: 24, height: 24, color: "text.secondary" }}
          >
            <Iconify icon="mdi:plus" width={18} />
          </IconButton>
        </Box>
      </Box>

      {/* Search */}
      <Box sx={{ px: 0.75, pb: 0.5 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search skills..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{
            "& .MuiInputBase-root": {
              fontSize: 12,
              height: 28,
              borderRadius: "6px",
              bgcolor: "transparent",
            },
            "& .MuiOutlinedInput-notchedOutline": {
              borderColor: isDark
                ? alpha(theme.palette.common.white, 0.06)
                : alpha(theme.palette.common.black, 0.06),
            },
          }}
        />
      </Box>

      {/* List */}
      <Box sx={{ flex: "1 1 0", minHeight: 0, overflow: "auto", px: 0.75 }}>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={18} sx={{ color: "text.disabled" }} />
          </Box>
        )}

        {!loading && filtered.length === 0 && (
          <Typography
            sx={{
              fontSize: 11,
              color: "text.disabled",
              textAlign: "center",
              py: 3,
            }}
          >
            {search ? "No matching skills" : "No skills yet"}
          </Typography>
        )}

        {filtered.map((skill) => {
          const isActive = skill.id === selectedId;
          return (
            <ListItemButton
              key={skill.id}
              selected={isActive}
              onClick={() => onSelect(skill)}
              sx={{
                borderRadius: "6px",
                mb: "1px",
                px: 1,
                py: "6px",
                minHeight: 0,
                color: isActive ? "primary.main" : "text.primary",
                bgcolor: isActive
                  ? alpha(theme.palette.primary.main, 0.08)
                  : "transparent",
                "&:hover": {
                  bgcolor: isActive
                    ? alpha(theme.palette.primary.main, 0.12)
                    : theme.palette.action.hover,
                },
                "&.Mui-selected": {
                  bgcolor: alpha(theme.palette.primary.main, 0.08),
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 26, color: "inherit" }}>
                <Iconify icon={skill.icon || "mdi:star-outline"} width={16} />
              </ListItemIcon>
              <ListItemText
                primary={
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <Typography
                      noWrap
                      sx={{ fontSize: 12, fontWeight: isActive ? 600 : 500 }}
                    >
                      {skill.name}
                    </Typography>
                    {(skill.is_builtin || skill.is_system) && (
                      <Chip
                        label="SYS"
                        size="small"
                        variant="outlined"
                        sx={{
                          height: 14,
                          fontSize: 8,
                          fontWeight: 700,
                          borderColor: alpha(theme.palette.info.main, 0.4),
                          color: "info.main",
                          "& .MuiChip-label": { px: 0.4 },
                        }}
                      />
                    )}
                  </Box>
                }
              />
            </ListItemButton>
          );
        })}
      </Box>

      {/* Create skill footer */}
      <Box sx={{ px: 0.75, pb: 1 }}>
        <Divider sx={{ mb: 0.5 }} />
        <ListItemButton
          onClick={onCreateClick}
          sx={{
            borderRadius: "6px",
            px: 1,
            py: "6px",
            minHeight: 0,
          }}
        >
          <ListItemIcon sx={{ minWidth: 26, color: "primary.main" }}>
            <Iconify icon="mdi:plus" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Create Skill"
            primaryTypographyProps={{
              fontSize: 12,
              fontWeight: 600,
              color: "primary.main",
            }}
          />
        </ListItemButton>
      </Box>
    </Box>
  );
}

SkillsListPanel.propTypes = {
  skills: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      name: PropTypes.string,
      description: PropTypes.string,
      icon: PropTypes.string,
      isBuiltin: PropTypes.bool,
      is_builtin: PropTypes.bool,
      is_system: PropTypes.bool,
      isSystem: PropTypes.bool,
    }),
  ).isRequired,
  selectedId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onSelect: PropTypes.func.isRequired,
  onCreateClick: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Panel 2: Connectors list
// ---------------------------------------------------------------------------
function ConnectorsListPanel({
  connectors,
  selectedId,
  onSelect,
  onCreateClick,
  loading,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [search, setSearch] = useState("");

  const grouped = useMemo(() => {
    let items = connectors;
    if (search.trim()) {
      const q = search.toLowerCase();
      items = connectors.filter(
        (c) =>
          c.name?.toLowerCase().includes(q) ||
          c.type?.toLowerCase().includes(q),
      );
    }
    const groups = {};
    for (const conn of items) {
      const cat = categorizeConnector(conn);
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(conn);
    }
    return groups;
  }, [connectors, search]);

  const categoryOrder = ["Web", "Desktop", "Data", "Cloud", "Custom"];

  return (
    <Box
      sx={{
        width: 250,
        minWidth: 250,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: (t) => `solid 1px ${t.palette.divider}`,
        height: "100%",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          px: 1.5,
          pt: 2,
          pb: 0.5,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography
          sx={{ fontSize: 13, fontWeight: 700, color: "text.primary" }}
        >
          Connectors
        </Typography>
        <IconButton
          size="small"
          onClick={onCreateClick}
          title="Add connector"
          sx={{ width: 24, height: 24, color: "text.secondary" }}
        >
          <Iconify icon="mdi:plus" width={18} />
        </IconButton>
      </Box>

      {/* Search */}
      <Box sx={{ px: 0.75, pb: 0.5 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search connectors..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{
            "& .MuiInputBase-root": {
              fontSize: 12,
              height: 28,
              borderRadius: "6px",
              bgcolor: "transparent",
            },
            "& .MuiOutlinedInput-notchedOutline": {
              borderColor: isDark
                ? alpha(theme.palette.common.white, 0.06)
                : alpha(theme.palette.common.black, 0.06),
            },
          }}
        />
      </Box>

      {/* List */}
      <Box sx={{ flex: "1 1 0", minHeight: 0, overflow: "auto", px: 0.75 }}>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={18} sx={{ color: "text.disabled" }} />
          </Box>
        )}

        {!loading &&
          categoryOrder.map((cat) => {
            const items = grouped[cat];
            if (!items || items.length === 0) return null;
            return (
              <React.Fragment key={cat}>
                <Typography
                  sx={{
                    fontSize: 10,
                    fontWeight: 600,
                    color: "text.disabled",
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                    px: 0.5,
                    pt: 1.5,
                    pb: 0.25,
                  }}
                >
                  {cat}
                </Typography>
                {items.map((conn) => {
                  const isActive = conn.id === selectedId;
                  const isCustom = conn.is_custom || cat === "Custom";
                  return (
                    <ListItemButton
                      key={conn.id}
                      selected={isActive}
                      onClick={() => onSelect(conn)}
                      sx={{
                        borderRadius: "6px",
                        mb: "1px",
                        px: 1,
                        py: "6px",
                        minHeight: 0,
                        color: isActive ? "primary.main" : "text.primary",
                        bgcolor: isActive
                          ? alpha(theme.palette.primary.main, 0.08)
                          : "transparent",
                        "&:hover": {
                          bgcolor: isActive
                            ? alpha(theme.palette.primary.main, 0.12)
                            : theme.palette.action.hover,
                        },
                        "&.Mui-selected": {
                          bgcolor: alpha(theme.palette.primary.main, 0.08),
                        },
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: 26, color: "inherit" }}>
                        <Iconify icon={getConnectorIcon(conn)} width={16} />
                      </ListItemIcon>
                      <ListItemText
                        primary={
                          <Box
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              gap: 0.75,
                            }}
                          >
                            <Typography
                              sx={{
                                fontSize: 12,
                                fontWeight: isActive ? 600 : 500,
                                noWrap: true,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                              }}
                            >
                              {conn.name}
                            </Typography>
                            {isCustom && (
                              <Chip
                                label="CUSTOM"
                                size="small"
                                variant="outlined"
                                sx={{
                                  height: 16,
                                  fontSize: 9,
                                  fontWeight: 700,
                                  letterSpacing: 0.3,
                                  borderColor: alpha(
                                    theme.palette.warning.main,
                                    0.5,
                                  ),
                                  color: "warning.main",
                                  "& .MuiChip-label": { px: 0.5 },
                                }}
                              />
                            )}
                          </Box>
                        }
                      />
                    </ListItemButton>
                  );
                })}
              </React.Fragment>
            );
          })}

        {!loading && Object.keys(grouped).length === 0 && (
          <Box sx={{ textAlign: "center", py: 4, px: 2 }}>
            <Iconify
              icon="mdi:puzzle-outline"
              width={32}
              sx={{ color: "text.disabled", mb: 1 }}
            />
            <Typography sx={{ fontSize: 12, color: "text.disabled", mb: 1.5 }}>
              {search ? "No matching connectors" : "No connectors added yet"}
            </Typography>
            {!search && (
              <Button
                size="small"
                variant="outlined"
                onClick={onCreateClick}
                sx={{
                  fontSize: 11,
                  textTransform: "none",
                  borderRadius: "8px",
                }}
              >
                Add connector
              </Button>
            )}
          </Box>
        )}
      </Box>
    </Box>
  );
}

ConnectorsListPanel.propTypes = {
  connectors: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      name: PropTypes.string,
      type: PropTypes.string,
      icon: PropTypes.string,
      is_custom: PropTypes.bool,
    }),
  ).isRequired,
  selectedId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onSelect: PropTypes.func.isRequired,
  onCreateClick: PropTypes.func.isRequired,
  loading: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Panel 3: Skill detail + inline editor
// ---------------------------------------------------------------------------
function SkillDetailPanel({
  skill,
  isEditing,
  onEdit,
  onDuplicate,
  onSaved,
  onCancelEdit,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isSystem = skill?.is_builtin || skill?.is_system;

  // ── Inline editor state ──
  const [form, setForm] = useState({
    name: "",
    description: "",
    icon: "mdi:star-outline",
    instructions: "",
    trigger_phrases: [],
  });
  const [phraseInput, setPhraseInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isEditing && skill) {
      setForm({
        name: skill.name || "",
        description: skill.description || "",
        icon: skill.icon || "mdi:star-outline",
        instructions: skill.instructions || "",
        trigger_phrases: skill.trigger_phrases || [],
      });
    } else if (isEditing && !skill) {
      setForm({
        name: "",
        description: "",
        icon: "mdi:star-outline",
        instructions: "",
        trigger_phrases: [],
      });
    }
    setError(null);
    setPhraseInput("");
  }, [isEditing, skill]);

  const handleChange = (field) => (e) =>
    setForm((p) => ({ ...p, [field]: e.target.value }));

  const handleAddPhrase = (e) => {
    if (e.key === "Enter" && phraseInput.trim()) {
      e.preventDefault();
      const phrase = phraseInput.trim();
      if (!form.trigger_phrases.includes(phrase)) {
        setForm((p) => ({
          ...p,
          trigger_phrases: [...p.trigger_phrases, phrase],
        }));
      }
      setPhraseInput("");
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    if (!form.instructions.trim()) {
      setError("Instructions are required");
      return;
    }
    if (form.trigger_phrases.length === 0) {
      setError("At least one trigger phrase is required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const { createSkill, updateSkill } = await import(
        "../hooks/useFalconAPI"
      );
      if (skill?.id && !skill.is_builtin && !skill.is_system) {
        await updateSkill(skill.id, form);
      } else {
        await createSkill(form);
      }
      onSaved?.();
    } catch (err) {
      setError(
        err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          "Failed to save",
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!skill?.id || isSystem) return;
    setSaving(true);
    try {
      const { deleteSkill } = await import("../hooks/useFalconAPI");
      await deleteSkill(skill.id);
      onSaved?.();
    } catch {
      /* silent */
    } finally {
      setSaving(false);
    }
  };

  // ── Empty state ──
  if (!skill && !isEditing) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: 4,
        }}
      >
        <Iconify
          icon="mdi:lightning-bolt"
          width={48}
          sx={{ color: "text.disabled", mb: 2 }}
        />
        <Typography sx={{ fontSize: 15, fontWeight: 600, mb: 0.5 }}>
          Create new skills
        </Typography>
        <Typography
          sx={{
            fontSize: 13,
            color: "text.secondary",
            textAlign: "center",
            maxWidth: 320,
          }}
        >
          Teach Falcon your processes, team norms, and expertise. Skills guide
          how the agent responds.
        </Typography>
      </Box>
    );
  }

  // ── Inline editor ──
  if (isEditing) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
          p: 3,
        }}
      >
        <Typography sx={{ fontSize: 16, fontWeight: 700, mb: 2.5 }}>
          {skill?.id ? "Edit Skill" : "Create Skill"}
        </Typography>

        {error && (
          <Typography sx={{ fontSize: 12, color: "error.main", mb: 1.5 }}>
            {error}
          </Typography>
        )}

        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            mb: 0.5,
          }}
        >
          Name{" "}
          <Typography component="span" sx={{ color: "error.main" }}>
            *
          </Typography>
        </Typography>
        <TextField
          size="small"
          fullWidth
          placeholder="e.g. Build a Dataset"
          required
          value={form.name}
          onChange={handleChange("name")}
          error={!!error && !form.name.trim()}
          sx={{
            mb: 2,
            "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
          }}
        />

        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            mb: 0.5,
          }}
        >
          Description
        </Typography>
        <TextField
          size="small"
          fullWidth
          placeholder="What does this skill do?"
          value={form.description}
          onChange={handleChange("description")}
          sx={{
            mb: 2,
            "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
          }}
        />

        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            mb: 0.5,
          }}
        >
          Instructions{" "}
          <Typography component="span" sx={{ color: "error.main" }}>
            *
          </Typography>
        </Typography>
        <TextField
          size="small"
          fullWidth
          multiline
          minRows={6}
          maxRows={16}
          required
          placeholder="Write detailed instructions for how Falcon should behave when this skill is active..."
          value={form.instructions}
          onChange={handleChange("instructions")}
          error={!!error && !form.instructions.trim()}
          sx={{
            mb: 2,
            "& .MuiInputBase-root": {
              fontSize: 13,
              borderRadius: "8px",
              fontFamily: "monospace",
            },
          }}
        />

        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            mb: 0.5,
          }}
        >
          Trigger phrases{" "}
          <Typography component="span" sx={{ color: "error.main" }}>
            *
          </Typography>{" "}
          <Typography
            component="span"
            sx={{ fontSize: 11, color: "text.disabled" }}
          >
            (press Enter to add)
          </Typography>
        </Typography>
        <TextField
          size="small"
          fullWidth
          placeholder="e.g. /build-dataset"
          error={!!error && form.trigger_phrases.length === 0}
          value={phraseInput}
          onChange={(e) => setPhraseInput(e.target.value)}
          onKeyDown={handleAddPhrase}
          sx={{
            mb: 1,
            "& .MuiInputBase-root": { fontSize: 13, borderRadius: "8px" },
          }}
        />
        {form.trigger_phrases.length > 0 && (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 2 }}>
            {form.trigger_phrases.map((p) => (
              <Chip
                key={p}
                label={p}
                size="small"
                onDelete={() =>
                  setForm((prev) => ({
                    ...prev,
                    trigger_phrases: prev.trigger_phrases.filter(
                      (x) => x !== p,
                    ),
                  }))
                }
                sx={{ fontSize: 11, height: 24 }}
              />
            ))}
          </Box>
        )}

        <Box sx={{ display: "flex", gap: 1, mt: 1 }}>
          <Button
            variant="contained"
            size="small"
            onClick={handleSave}
            disabled={saving}
            sx={{
              textTransform: "none",
              borderRadius: "8px",
              fontSize: 13,
              px: 3,
            }}
          >
            {saving ? "Saving..." : "Save"}
          </Button>
          <Button
            variant="outlined"
            size="small"
            onClick={onCancelEdit}
            sx={{ textTransform: "none", borderRadius: "8px", fontSize: 13 }}
          >
            Cancel
          </Button>
          {skill?.id && !isSystem && (
            <Button
              variant="text"
              size="small"
              color="error"
              onClick={handleDelete}
              disabled={saving}
              sx={{
                textTransform: "none",
                borderRadius: "8px",
                fontSize: 13,
                ml: "auto",
              }}
            >
              Delete
            </Button>
          )}
        </Box>
      </Box>
    );
  }

  // ── Read-only detail ──
  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        p: 3,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          mb: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: "10px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: "primary.main",
            }}
          >
            <Iconify icon={skill.icon || "mdi:star-outline"} width={22} />
          </Box>
          <Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography sx={{ fontSize: 16, fontWeight: 700 }}>
                {skill.name}
              </Typography>
              {isSystem && (
                <Chip
                  label="SYSTEM"
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 18,
                    fontSize: 9,
                    fontWeight: 700,
                    letterSpacing: 0.5,
                    borderColor: alpha(theme.palette.info.main, 0.4),
                    color: "info.main",
                  }}
                />
              )}
            </Box>
            {skill.description && (
              <Typography
                sx={{ fontSize: 13, color: "text.secondary", mt: 0.25 }}
              >
                {skill.description}
              </Typography>
            )}
          </Box>
        </Box>

        <Box sx={{ display: "flex", gap: 0.5 }}>
          {isSystem ? (
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mdi:content-copy" width={14} />}
              onClick={() => onDuplicate(skill)}
              sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
            >
              Duplicate
            </Button>
          ) : (
            <IconButton
              size="small"
              onClick={() => onEdit(skill)}
              title="Edit"
              sx={{ color: "text.secondary" }}
            >
              <Iconify icon="mdi:pencil-outline" width={18} />
            </IconButton>
          )}
        </Box>
      </Box>

      {/* Metadata */}
      <Box sx={{ display: "flex", gap: 4, mb: 2 }}>
        <Box>
          <Typography
            sx={{
              fontSize: 11,
              fontWeight: 600,
              color: "text.disabled",
              mb: 0.25,
            }}
          >
            Added by
          </Typography>
          <Typography sx={{ fontSize: 13 }}>
            {skill.created_by_display || (isSystem ? "System" : "You")}
          </Typography>
        </Box>
      </Box>

      {/* Trigger phrases */}
      {skill.trigger_phrases?.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography
            sx={{
              fontSize: 11,
              fontWeight: 600,
              color: "text.disabled",
              mb: 0.5,
            }}
          >
            Trigger phrases
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            {skill.trigger_phrases.map((phrase) => (
              <Chip
                key={phrase}
                label={phrase}
                size="small"
                variant="outlined"
                sx={{
                  fontSize: 11,
                  height: 24,
                }}
              />
            ))}
          </Box>
        </Box>
      )}

      {/* Description */}
      {skill.description && (
        <Box sx={{ mb: 2 }}>
          <Typography
            sx={{
              fontSize: 11,
              fontWeight: 600,
              color: "text.disabled",
              mb: 0.5,
            }}
          >
            Description
          </Typography>
          <Typography
            sx={{ fontSize: 13, color: "text.secondary", lineHeight: 1.6 }}
          >
            {skill.description}
          </Typography>
        </Box>
      )}

      {/* Instructions */}
      <Box sx={{ mb: 2 }}>
        <Typography
          sx={{
            fontSize: 11,
            fontWeight: 600,
            color: "text.disabled",
            mb: 0.5,
          }}
        >
          Instructions
        </Typography>
        {skill.instructions ? (
          <Box
            sx={{
              p: 2,
              borderRadius: "8px",
              bgcolor: isDark
                ? alpha(theme.palette.common.white, 0.03)
                : alpha(theme.palette.common.black, 0.02),
              border: (t) => `1px solid ${t.palette.divider}`,
            }}
          >
            <Typography
              component="pre"
              sx={{
                fontSize: 13,
                lineHeight: 1.7,
                color: "text.secondary",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                m: 0,
              }}
            >
              {skill.instructions}
            </Typography>
          </Box>
        ) : (
          <Typography
            sx={{ fontSize: 13, color: "text.disabled", fontStyle: "italic" }}
          >
            No instructions defined yet.{" "}
            {!isSystem && "Click Edit to add instructions."}
          </Typography>
        )}
      </Box>

      {/* Spacer to push edit button to bottom */}
      <Box sx={{ flex: 1 }} />

      {/* Edit button at bottom for custom skills */}
      {!isSystem && (
        <Box sx={{ pt: 2, borderTop: (t) => `1px solid ${t.palette.divider}` }}>
          <Button
            fullWidth
            variant="outlined"
            startIcon={<Iconify icon="mdi:pencil-outline" width={16} />}
            onClick={() => onEdit(skill)}
            sx={{ textTransform: "none", borderRadius: "8px", fontSize: 13 }}
          >
            Edit skill
          </Button>
        </Box>
      )}
    </Box>
  );
}

SkillDetailPanel.propTypes = {
  skill: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.string,
    description: PropTypes.string,
    icon: PropTypes.string,
    instructions: PropTypes.string,
    trigger_phrases: PropTypes.arrayOf(PropTypes.string),
    is_builtin: PropTypes.bool,
    is_system: PropTypes.bool,
    created_by_display: PropTypes.string,
  }),
  isEditing: PropTypes.bool,
  onEdit: PropTypes.func,
  onDuplicate: PropTypes.func,
  onSaved: PropTypes.func,
  onCancelEdit: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Panel 3: Connector detail + inline editor
// ---------------------------------------------------------------------------
function ConnectorDetailPanel({
  connector,
  isEditing,
  onDisconnect,
  onReauth,
  onToolToggle,
  onSaved,
  onCancelEdit,
  onEdit,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  // ── Inline editor state ──
  const [form, setForm] = useState({
    name: "",
    server_url: "",
    api_key: "",
    oauth_client_id: "",
    oauth_client_secret: "",
  });
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isEditing && connector?.id) {
      setForm({
        name: connector.name || "",
        server_url: connector.server_url || connector.serverUrl || "",
        api_key: "",
        oauth_client_id:
          connector.oauth_client_id || connector.oauthClientId || "",
        oauth_client_secret: "",
      });
      setShowAdvanced(!!(connector.oauth_client_id || connector.oauthClientId));
    } else if (isEditing) {
      setForm({
        name: "",
        server_url: "",
        api_key: "",
        oauth_client_id: "",
        oauth_client_secret: "",
      });
      setShowAdvanced(false);
    }
    setError(null);
  }, [isEditing, connector]);

  const handleChange = (field) => (e) =>
    setForm((p) => ({ ...p, [field]: e.target.value }));

  const handleSave = async () => {
    if (!form.name.trim() || !form.server_url.trim()) {
      setError("Name and Server URL are required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const { createConnector, updateConnector } = await import(
        "../hooks/useFalconAPI"
      );
      const payload = { ...form };
      // Don't send empty optional fields
      if (!payload.api_key) delete payload.api_key;
      if (!payload.oauth_client_id) delete payload.oauth_client_id;
      if (!payload.oauth_client_secret) delete payload.oauth_client_secret;

      let result;
      if (connector?.id) {
        result = await updateConnector(connector.id, payload);
      } else {
        result = await createConnector(payload);
      }

      const connectorData = result?.result || result;
      onSaved?.();

      // After creating/updating, auto-trigger authentication
      if (connectorData?.id) {
        try {
          const { authenticateConnector } = await import(
            "../hooks/useFalconAPI"
          );
          const authResult = await authenticateConnector(connectorData.id);
          const authUrl =
            authResult?.authorization_url ||
            authResult?.auth_url ||
            authResult?.oauth_url;
          if (authUrl) {
            window.open(
              authUrl,
              "falcon_oauth",
              "width=600,height=700,popup=yes",
            );
          }
        } catch {
          // Auth will be retried via Re-authenticate button
        }
      }
    } catch (err) {
      setError(
        err?.response?.data?.error ||
          err?.response?.data?.detail ||
          err?.message ||
          "Failed to save",
      );
    } finally {
      setSaving(false);
    }
  };

  // ── Empty state ──
  if (!connector && !isEditing) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: 4,
        }}
      >
        <Iconify
          icon="mdi:puzzle-outline"
          width={48}
          sx={{ color: "text.disabled", mb: 2 }}
        />
        <Typography sx={{ fontSize: 15, fontWeight: 600, mb: 0.5 }}>
          Connect your tools
        </Typography>
        <Typography
          sx={{
            fontSize: 13,
            color: "text.secondary",
            textAlign: "center",
            maxWidth: 320,
          }}
        >
          Add MCP connectors to give Falcon access to external services like
          GitHub, Slack, databases, and more.
        </Typography>
      </Box>
    );
  }

  // ── Inline editor ──
  if (isEditing) {
    const isNew = !connector?.id;
    const inputSx = {
      mb: 2,
      "& .MuiInputBase-root": {
        fontSize: 14,
        borderRadius: "10px",
        height: 48,
        bgcolor: isDark
          ? alpha(theme.palette.common.white, 0.04)
          : alpha(theme.palette.common.black, 0.02),
      },
      "& .MuiOutlinedInput-notchedOutline": {
        borderColor: isDark
          ? alpha(theme.palette.common.white, 0.12)
          : alpha(theme.palette.common.black, 0.15),
      },
    };

    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
          p: 4,
          maxWidth: 600,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
          <Typography sx={{ fontSize: 20, fontWeight: 700 }}>
            {isNew ? "Add custom connector" : "Edit connector"}
          </Typography>
          {isNew && (
            <Chip
              label="BETA"
              size="small"
              variant="outlined"
              sx={{
                height: 20,
                fontSize: 10,
                fontWeight: 700,
                borderColor: alpha(theme.palette.info.main, 0.4),
                color: "info.main",
              }}
            />
          )}
        </Box>
        <Typography sx={{ fontSize: 13, color: "text.secondary", mb: 3 }}>
          Connect Falcon to your data and tools.
        </Typography>

        {error && (
          <Box
            sx={{
              mb: 2,
              p: 1.5,
              borderRadius: "8px",
              bgcolor: alpha(theme.palette.error.main, 0.08),
            }}
          >
            <Typography sx={{ fontSize: 12, color: "error.main" }}>
              {error}
            </Typography>
          </Box>
        )}

        <TextField
          fullWidth
          placeholder="Name"
          value={form.name}
          onChange={handleChange("name")}
          sx={inputSx}
        />

        <TextField
          fullWidth
          placeholder="Remote MCP server URL"
          value={form.server_url}
          onChange={handleChange("server_url")}
          sx={inputSx}
        />

        {/* Advanced settings — collapsible */}
        <Box
          onClick={() => setShowAdvanced(!showAdvanced)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            mb: 2,
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <Iconify
            icon={showAdvanced ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={20}
            sx={{ color: "text.secondary" }}
          />
          <Typography sx={{ fontSize: 14, fontWeight: 600 }}>
            Advanced settings
          </Typography>
        </Box>

        {showAdvanced && (
          <>
            <TextField
              fullWidth
              placeholder="OAuth Client ID (optional)"
              value={form.oauth_client_id}
              onChange={handleChange("oauth_client_id")}
              sx={inputSx}
            />
            <TextField
              fullWidth
              placeholder="OAuth Client Secret (optional)"
              type="password"
              value={form.oauth_client_secret}
              onChange={handleChange("oauth_client_secret")}
              sx={inputSx}
            />
            <TextField
              fullWidth
              placeholder="API Key / Bearer Token (optional)"
              type="password"
              value={form.api_key}
              onChange={handleChange("api_key")}
              sx={inputSx}
            />
          </>
        )}

        {/* Trust warning */}
        <Typography
          sx={{ fontSize: 12, color: "text.disabled", lineHeight: 1.6, mb: 3 }}
        >
          Only use connectors from developers you trust. Future AGI does not
          control which tools developers make available and cannot verify that
          they will work as intended or that they won&apos;t change.
        </Typography>

        {/* Buttons */}
        <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1.5 }}>
          <Button
            variant="outlined"
            onClick={onCancelEdit}
            sx={{
              textTransform: "none",
              borderRadius: "10px",
              fontSize: 14,
              px: 3,
              py: 1,
              minWidth: 100,
            }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={saving}
            sx={{
              textTransform: "none",
              borderRadius: "10px",
              fontSize: 14,
              px: 3,
              py: 1,
              minWidth: 100,
            }}
          >
            {saving ? "Adding..." : isNew ? "Add" : "Save"}
          </Button>
        </Box>
      </Box>
    );
  }

  const tools = connector.tools || connector.discovered_tools || [];
  const enabledNames =
    connector.enabled_tool_names || connector.enabled_tools || [];
  const isVerified = connector.is_verified ?? false;
  const isActive = connector.is_active ?? true;
  const lastError = connector.last_error || "";
  const authType = connector.auth_type || "none";

  // Split tools into interactive (write) and read-only
  const interactiveTools = tools.filter((t) => {
    const name = (t.name || "").toLowerCase();
    return (
      name.startsWith("create") ||
      name.startsWith("update") ||
      name.startsWith("delete") ||
      name.startsWith("send") ||
      name.startsWith("post") ||
      name.startsWith("run") ||
      name.startsWith("execute") ||
      name.startsWith("write") ||
      name.startsWith("add")
    );
  });
  const readOnlyTools = tools.filter((t) => !interactiveTools.includes(t));

  const isToolEnabled = (tool) => {
    if (enabledNames.length === 0 && tool.enabled !== false) return true;
    return enabledNames.includes(tool.name) || tool.enabled === true;
  };

  return (
    <Box
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        p: 3,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          mb: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: "10px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              color: "primary.main",
            }}
          >
            <Iconify icon={getConnectorIcon(connector)} width={22} />
          </Box>
          <Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography sx={{ fontSize: 16, fontWeight: 700 }}>
                {connector.name}
              </Typography>
              <Chip
                label={
                  isActive ? (isVerified ? "Connected" : "Pending") : "Inactive"
                }
                size="small"
                variant="outlined"
                color={
                  isActive && isVerified
                    ? "success"
                    : isActive
                      ? "warning"
                      : "default"
                }
                sx={{ height: 20, fontSize: 10, fontWeight: 600 }}
              />
            </Box>
            {connector.description && (
              <Typography
                sx={{ fontSize: 13, color: "text.secondary", mt: 0.25 }}
              >
                {connector.description}
              </Typography>
            )}
          </Box>
        </Box>
      </Box>

      {/* Server info */}
      <Box
        sx={{
          mb: 2,
          p: 1.5,
          borderRadius: "8px",
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.03)
            : alpha(theme.palette.common.black, 0.02),
        }}
      >
        <Typography sx={{ fontSize: 11, color: "text.disabled", mb: 0.25 }}>
          Server URL
        </Typography>
        <Typography
          sx={{
            fontSize: 12,
            fontFamily: "monospace",
            color: "text.secondary",
          }}
        >
          {connector.server_url || connector.serverUrl}
        </Typography>
        {authType !== "none" && (
          <Box sx={{ mt: 0.75 }}>
            <Typography sx={{ fontSize: 11, color: "text.disabled", mb: 0.25 }}>
              Authentication
            </Typography>
            <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
              {authType === "api_key"
                ? "API Key"
                : authType === "bearer"
                  ? "Bearer Token"
                  : authType}
            </Typography>
          </Box>
        )}
      </Box>

      {/* Error banner */}
      {lastError && (
        <Box
          sx={{
            mb: 2,
            p: 1.5,
            borderRadius: "8px",
            bgcolor: alpha(theme.palette.error.main, 0.08),
            border: `1px solid ${alpha(theme.palette.error.main, 0.2)}`,
          }}
        >
          <Typography sx={{ fontSize: 12, color: "error.main" }}>
            {lastError}
          </Typography>
        </Box>
      )}

      {/* Action buttons */}
      <Box sx={{ display: "flex", gap: 1, mb: 3 }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:pencil-outline" width={14} />}
          onClick={() => onEdit(connector)}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          Edit
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:refresh" width={14} />}
          onClick={() => onReauth?.(connector.id)}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          Re-authenticate
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="error"
          startIcon={<Iconify icon="mdi:link-variant-off" width={14} />}
          onClick={() => onDisconnect(connector.id)}
          sx={{ textTransform: "none", borderRadius: "8px", fontSize: 12 }}
        >
          Disconnect
        </Button>
      </Box>

      {/* Tool permissions */}
      {tools.length > 0 && (
        <Box>
          <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1.5 }}>
            Tool permissions
          </Typography>
          <Typography sx={{ fontSize: 12, color: "text.secondary", mb: 2 }}>
            Choose when Falcon is allowed to use these tools.
          </Typography>

          {/* Interactive tools */}
          {interactiveTools.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  mb: 0.75,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                  <Iconify
                    icon="mdi:cursor-default-click"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                  <Typography
                    sx={{
                      fontSize: 12,
                      fontWeight: 600,
                      color: "text.secondary",
                    }}
                  >
                    Interactive tools
                  </Typography>
                  <Chip
                    label={interactiveTools.length}
                    size="small"
                    variant="outlined"
                    sx={{ height: 18, fontSize: 10 }}
                  />
                </Box>
                <Chip
                  label="Always allow"
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 22,
                    fontSize: 10,
                    borderRadius: "6px",
                    cursor: "pointer",
                  }}
                  onClick={() =>
                    interactiveTools.forEach((t) => {
                      if (!isToolEnabled(t)) onToolToggle(connector.id, t);
                    })
                  }
                />
              </Box>
              <Box
                sx={{
                  borderRadius: "8px",
                  border: (t) => `1px solid ${t.palette.divider}`,
                  overflow: "hidden",
                }}
              >
                {interactiveTools.map((tool, idx) => (
                  <ToolRow
                    key={tool.name || idx}
                    tool={tool}
                    enabled={isToolEnabled(tool)}
                    onToggle={() => onToolToggle(connector.id, tool)}
                    isLast={idx === interactiveTools.length - 1}
                    isDark={isDark}
                    theme={theme}
                  />
                ))}
              </Box>
            </Box>
          )}

          {/* Read-only tools */}
          {readOnlyTools.length > 0 && (
            <Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  mb: 0.75,
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                  <Iconify
                    icon="mdi:eye-outline"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                  <Typography
                    sx={{
                      fontSize: 12,
                      fontWeight: 600,
                      color: "text.secondary",
                    }}
                  >
                    Read-only tools
                  </Typography>
                  <Chip
                    label={readOnlyTools.length}
                    size="small"
                    variant="outlined"
                    sx={{ height: 18, fontSize: 10 }}
                  />
                </Box>
                <Chip
                  label="Always allow"
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 22,
                    fontSize: 10,
                    borderRadius: "6px",
                    cursor: "pointer",
                  }}
                  onClick={() =>
                    readOnlyTools.forEach((t) => {
                      if (!isToolEnabled(t)) onToolToggle(connector.id, t);
                    })
                  }
                />
              </Box>
              <Box
                sx={{
                  borderRadius: "8px",
                  border: (t) => `1px solid ${t.palette.divider}`,
                  overflow: "hidden",
                }}
              >
                {readOnlyTools.map((tool, idx) => (
                  <ToolRow
                    key={tool.name || idx}
                    tool={tool}
                    enabled={isToolEnabled(tool)}
                    onToggle={() => onToolToggle(connector.id, tool)}
                    isLast={idx === readOnlyTools.length - 1}
                    isDark={isDark}
                    theme={theme}
                  />
                ))}
              </Box>
            </Box>
          )}
        </Box>
      )}

      {tools.length === 0 && (
        <Box sx={{ textAlign: "center", py: 3 }}>
          <Iconify
            icon="mdi:tools"
            width={28}
            sx={{ color: "text.disabled", mb: 1 }}
          />
          <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
            No tools discovered yet. Tools will appear after the connector
            successfully connects.
          </Typography>
        </Box>
      )}
    </Box>
  );
}

const connectorToolShape = PropTypes.shape({
  name: PropTypes.string,
  id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  description: PropTypes.string,
  enabled: PropTypes.bool,
});

ConnectorDetailPanel.propTypes = {
  connector: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    name: PropTypes.string,
    description: PropTypes.string,
    icon: PropTypes.string,
    type: PropTypes.string,
    server_url: PropTypes.string,
    serverUrl: PropTypes.string,
    tools: PropTypes.arrayOf(connectorToolShape),
    discovered_tools: PropTypes.arrayOf(connectorToolShape),
    enabled_tool_names: PropTypes.arrayOf(PropTypes.string),
    is_verified: PropTypes.bool,
    is_active: PropTypes.bool,
    is_custom: PropTypes.bool,
    last_error: PropTypes.string,
    auth_type: PropTypes.string,
    oauth_client_id: PropTypes.string,
    oauthClientId: PropTypes.string,
  }),
  isEditing: PropTypes.bool,
  onDisconnect: PropTypes.func,
  onReauth: PropTypes.func,
  onToolToggle: PropTypes.func,
  onSaved: PropTypes.func,
  onCancelEdit: PropTypes.func,
  onEdit: PropTypes.func,
};

// Tool row component for clean per-item rendering
function ToolRow({ tool, enabled, onToggle, isLast, isDark, theme }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        px: 2,
        py: 1,
        borderBottom: isLast ? "none" : (t) => `1px solid ${t.palette.divider}`,
        "&:hover": {
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.02)
            : alpha(theme.palette.common.black, 0.01),
        },
      }}
    >
      <Box sx={{ flex: 1, mr: 1 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
          {tool.name || tool.id}
        </Typography>
        {tool.description && (
          <Typography
            noWrap
            sx={{
              fontSize: 11,
              color: "text.secondary",
              mt: 0.15,
              maxWidth: 400,
            }}
          >
            {tool.description}
          </Typography>
        )}
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
        <IconButton
          size="small"
          onClick={onToggle}
          title={enabled ? "Allowed" : "Denied"}
          sx={{
            width: 26,
            height: 26,
            color: enabled ? "success.main" : "text.disabled",
          }}
        >
          <Iconify
            icon={enabled ? "mdi:check-circle" : "mdi:close-circle-outline"}
            width={18}
          />
        </IconButton>
      </Box>
    </Box>
  );
}

ToolRow.propTypes = {
  tool: PropTypes.shape({
    name: PropTypes.string,
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    description: PropTypes.string,
    enabled: PropTypes.bool,
  }).isRequired,
  enabled: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  isLast: PropTypes.bool,
  isDark: PropTypes.bool,
  theme: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function CustomizePanel() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const [selectedTab, setSelectedTab] = useState("skills");
  const [skills, setSkills] = useState([]);
  const [connectors, setConnectors] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [loadingSkills, setLoadingSkills] = useState(false);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [isEditingSkill, setIsEditingSkill] = useState(false);
  const [isEditingConnector, setIsEditingConnector] = useState(false);

  // ---- Load data ----
  const loadSkills = useCallback(async () => {
    setLoadingSkills(true);
    try {
      const data = await listSkills();
      const results = data.results || data || [];
      const skillList = Array.isArray(results) ? results : [];
      setSkills(skillList);
      // Also update the global store so SlashCommandPicker sees new skills
      useFalconStore.getState().setSkills(skillList);
    } catch {
      // silent
    } finally {
      setLoadingSkills(false);
    }
  }, []);

  const loadConnectors = useCallback(async () => {
    setLoadingConnectors(true);
    try {
      const data = await fetchConnectors();
      const results = data.results || data || [];
      setConnectors(Array.isArray(results) ? results : []);
    } catch {
      // silent
    } finally {
      setLoadingConnectors(false);
    }
  }, []);

  useEffect(() => {
    loadSkills();
    loadConnectors();
  }, [loadSkills, loadConnectors]);

  // ---- Handlers ----
  const handleTabChange = useCallback((tab) => {
    setSelectedTab(tab);
    setSelectedItem(null);
  }, []);

  const handleSelectItem = useCallback(
    async (item) => {
      setSelectedItem(item); // show immediately with list data
      setIsEditingSkill(false);
      setIsEditingConnector(false);

      // For skills, fetch full detail (includes instructions)
      if (selectedTab === "skills" && item?.id) {
        try {
          const { getSkill } = await import("../hooks/useFalconAPI");
          const resp = await getSkill(item.id);
          const fullSkill = resp.result || resp;
          setSelectedItem(fullSkill);
        } catch {
          // keep partial data from list
        }
      }
    },
    [selectedTab],
  );

  const handleCreateSkill = useCallback(() => {
    setSelectedItem(null);
    setIsEditingSkill(true);
  }, []);

  const handleEditSkill = useCallback((skill) => {
    setSelectedItem(skill);
    setIsEditingSkill(true);
  }, []);

  const handleDuplicateSkill = useCallback((skill) => {
    // Pre-fill form with skill data but clear id so it creates a new one
    setSelectedItem({
      ...skill,
      id: null,
      name: `${skill.name} (copy)`,
      is_system: false,
      is_builtin: false,
    });
    setIsEditingSkill(true);
  }, []);

  const handleSkillSaved = useCallback(() => {
    loadSkills();
    setSelectedItem(null);
    setIsEditingSkill(false);
  }, [loadSkills]);

  const handleCreateConnector = useCallback(() => {
    setSelectedItem(null);
    setIsEditingConnector(true);
  }, []);

  const handleEditConnector = useCallback((conn) => {
    setSelectedItem(conn);
    setIsEditingConnector(true);
  }, []);

  const handleConnectorSaved = useCallback(() => {
    loadConnectors();
    setSelectedItem(null);
    setIsEditingConnector(false);
  }, [loadConnectors]);

  // Listen for OAuth callback postMessage from popup
  useEffect(() => {
    const handler = async (event) => {
      if (event.data?.type !== "falcon_oauth_callback") return;
      if (event.data.status !== "success") return;

      const connectorId = selectedItem?.id;

      // Refresh the connectors list first
      await loadConnectors();

      // If we have a selected connector, trigger tool discovery and refresh it
      if (connectorId) {
        try {
          // Backend already discovers tools during OAuth callback, but call
          // discover again to ensure we have the latest tools and to handle
          // any race conditions where the callback hasn't finished yet.
          await discoverConnectorTools(connectorId);
        } catch {
          // Tool discovery may have already been done by the backend callback;
          // if this fails, the connector data from loadConnectors is still valid.
        }

        // Fetch the updated connector list to get the connector with tools
        try {
          const data = await fetchConnectors();
          const results = data.results || data || [];
          const updated = results.find((c) => c.id === connectorId);
          if (updated) setSelectedItem(updated);
        } catch {
          // silent — loadConnectors already refreshed the list
        }
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [loadConnectors, selectedItem?.id]);

  const handleReauth = useCallback(
    async (connectorId) => {
      try {
        const { authenticateConnector } = await import("../hooks/useFalconAPI");
        const result = await authenticateConnector(connectorId);
        // Check for OAuth authorization URL (API may return camelCase or snake_case)
        const authUrl =
          result?.authorizationUrl ||
          result?.authorization_url ||
          result?.auth_url ||
          result?.oauth_url ||
          result?.result?.authorizationUrl ||
          result?.result?.authorization_url;
        if (authUrl) {
          window.open(
            authUrl,
            "falcon_oauth",
            "width=600,height=700,popup=yes",
          );
          return; // postMessage handler will refresh when popup completes
        }
        // Non-OAuth: result contains updated connector
        loadConnectors();
        const updatedConnector = result?.result || result;
        if (updatedConnector?.id) {
          setSelectedItem(updatedConnector);
        }
      } catch (err) {
        const connector = connectors.find((c) => c.id === connectorId);
        if (connector) {
          setSelectedItem({
            ...connector,
            last_error:
              err?.response?.data?.error ||
              err?.response?.data?.message ||
              "Authentication failed",
          });
        }
      }
    },
    [loadConnectors, connectors],
  );

  const handleDisconnect = useCallback(async (connectorId) => {
    try {
      await deleteConnector(connectorId);
      setConnectors((prev) => prev.filter((c) => c.id !== connectorId));
      setSelectedItem(null);
    } catch {
      // silent
    }
  }, []);

  const handleToolToggle = useCallback(
    async (connectorId, tool) => {
      const conn = connectors.find((c) => c.id === connectorId);
      if (!conn) return;
      const toolsForConnector = conn.tools || conn.discovered_tools || [];
      const toolNames = toolsForConnector
        .map((t) => t.name || t.id)
        .filter(Boolean);
      const currentEnabled =
        (conn.enabled_tool_names || conn.enabled_tools || []).length === 0 &&
        toolNames.length > 0
          ? toolNames
          : conn.enabled_tool_names || conn.enabled_tools || [];
      const targetName = tool.name || tool.id;
      const isEnabled = currentEnabled.includes(targetName);
      const updatedEnabledNames = isEnabled
        ? currentEnabled.filter((name) => name !== targetName)
        : [...new Set([...currentEnabled, targetName])];
      try {
        await updateConnectorTools(connectorId, updatedEnabledNames);
        setConnectors((prev) =>
          prev.map((c) =>
            c.id === connectorId
              ? {
                  ...c,
                  enabled_tool_names: updatedEnabledNames,
                  enabled_tools: updatedEnabledNames,
                }
              : c,
          ),
        );
        setSelectedItem((prev) =>
          prev?.id === connectorId
            ? {
                ...prev,
                enabled_tool_names: updatedEnabledNames,
                enabled_tools: updatedEnabledNames,
              }
            : prev,
        );
      } catch {
        // silent
      }
    },
    [connectors],
  );

  // ---- Render ----
  return (
    <>
      <Box
        sx={{
          display: "flex",
          height: "100%",
          width: "100%",
          bgcolor: isDark
            ? alpha(theme.palette.background.default, 0.6)
            : theme.palette.background.default,
        }}
      >
        {/* Panel 1: Navigation */}
        <NavigationPanel
          selectedTab={selectedTab}
          onTabChange={handleTabChange}
        />

        {/* Panel 2: List */}
        {selectedTab === "skills" ? (
          <SkillsListPanel
            skills={skills}
            selectedId={selectedItem?.id}
            onSelect={handleSelectItem}
            onCreateClick={handleCreateSkill}
            loading={loadingSkills}
          />
        ) : (
          <ConnectorsListPanel
            connectors={connectors}
            selectedId={selectedItem?.id}
            onSelect={handleSelectItem}
            onCreateClick={handleCreateConnector}
            loading={loadingConnectors}
          />
        )}

        {/* Panel 3: Detail */}
        {selectedTab === "skills" ? (
          <SkillDetailPanel
            skill={selectedItem}
            isEditing={isEditingSkill}
            onEdit={handleEditSkill}
            onDuplicate={handleDuplicateSkill}
            onSaved={handleSkillSaved}
            onCancelEdit={() => setIsEditingSkill(false)}
          />
        ) : (
          <ConnectorDetailPanel
            connector={selectedItem}
            isEditing={isEditingConnector}
            onDisconnect={handleDisconnect}
            onReauth={handleReauth}
            onToolToggle={handleToolToggle}
            onEdit={handleEditConnector}
            onSaved={handleConnectorSaved}
            onCancelEdit={() => setIsEditingConnector(false)}
          />
        )}
      </Box>
    </>
  );
}
