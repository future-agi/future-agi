import React, { useState, useRef, useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import Chip from "@mui/material/Chip";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Divider from "@mui/material/Divider";
import Popover from "@mui/material/Popover";
import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import ListItemButton from "@mui/material/ListItemButton";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import { useFalconContext } from "../hooks/useFalconContext";
import { fetchConversations, getConversation } from "../hooks/useFalconAPI";
import SkillEditorDialog from "./SkillEditorDialog";

const CONTEXT_ICONS = {
  datasets: "solar:database-linear",
  evaluations: "solar:checklist-minimalistic-linear",
  tracing: "solar:chart-2-linear",
  experiments: "solar:test-tube-linear",
  prompts: "solar:code-linear",
  settings: "solar:settings-linear",
  general: null,
};

const CONTEXT_LABELS = {
  datasets: "Datasets",
  evaluations: "Evaluations",
  tracing: "Tracing",
  experiments: "Experiments",
  prompts: "Prompts",
  settings: "Settings",
  general: null,
};

export default function FalconAIHeader({ onClose, mode = "sidebar" }) {
  const theme = useTheme();
  const context = useFalconContext();

  const resetChat = useFalconStore((s) => s.resetChat);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const conversations = useFalconStore((s) => s.conversations);
  const skills = useFalconStore((s) => s.skills);

  // Get current conversation title
  const currentConv = conversations.find((c) => c.id === currentConversationId);
  const headerTitle =
    currentConv?.title && currentConv.title !== "New conversation"
      ? currentConv.title
      : "Falcon AI";
  const setPendingPrompt = useFalconStore((s) => s.setPendingPrompt);
  const skillsMenuTrigger = useFalconStore((s) => s.skillsMenuTrigger);

  const [skillsAnchor, setSkillsAnchor] = useState(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [historyAnchor, setHistoryAnchor] = useState(null);
  const [historyItems, setHistoryItems] = useState([]);
  const [historySearch, setHistorySearch] = useState("");
  const [historyHasMore, setHistoryHasMore] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const historyListRef = useRef(null);
  const skillsBtnRef = useRef(null);

  const skillsMenuOpen = Boolean(skillsAnchor);
  const historyOpen = Boolean(historyAnchor);

  const PAGE_SIZE = 20;

  const loadHistory = useCallback(async (offset = 0, search = "") => {
    setHistoryLoading(true);
    try {
      const data = await fetchConversations({
        limit: PAGE_SIZE,
        offset,
        search,
      });
      const results = data.results || [];
      if (offset === 0) {
        setHistoryItems(results);
      } else {
        setHistoryItems((prev) => [...prev, ...results]);
      }
      setHistoryHasMore(data.has_more ?? results.length >= PAGE_SIZE);
    } catch {
      // silent
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleHistoryOpen = (e) => {
    setHistoryAnchor(e.currentTarget);
    setHistorySearch("");
    setHistoryItems([]);
    loadHistory(0, "");
  };

  const handleHistorySelect = async (conv) => {
    setHistoryAnchor(null);
    setCurrentConversation(conv.id);
    try {
      const data = await getConversation(conv.id);
      const c = data.result || data;
      const msgs = (c.messages || []).map((m) => ({
        ...m,
        tool_calls: m.tool_calls || [],
        completion_card: m.completion_card || null,
        created_at: m.created_at,
      }));
      useFalconStore.getState().setMessages(msgs);
    } catch {
      // silent
    }
  };

  const handleHistoryScroll = () => {
    const el = historyListRef.current;
    if (!el || !historyHasMore || historyLoading) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 50) {
      loadHistory(historyItems.length, historySearch);
    }
  };

  // Debounced search for history
  useEffect(() => {
    if (!historyOpen) return undefined;
    const t = setTimeout(() => loadHistory(0, historySearch), 300);
    return () => clearTimeout(t);
  }, [historySearch, historyOpen, loadHistory]);

  // Open skills menu when triggered from slash command
  useEffect(() => {
    if (skillsMenuTrigger > 0 && skillsBtnRef.current) {
      setSkillsAnchor(skillsBtnRef.current);
    }
  }, [skillsMenuTrigger]);

  const handleNewChat = () => {
    resetChat();
    setCurrentConversation(null);
  };

  const handleSkillClick = (skill) => {
    // Insert `/<slug> ` into the input; ChatInput will focus and place the
    // caret at the end. No activeSkill state, no header chip.
    setPendingPrompt(`/${skill.slug} `);
    setSkillsAnchor(null);
  };

  const handleCreateSkill = () => {
    setSkillsAnchor(null);
    setEditorOpen(true);
  };

  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2.5,
          py: 1.5,
          borderBottom: 1,
          borderColor: "divider",
          minHeight: 52,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
          <Typography
            variant="subtitle1"
            sx={{
              fontWeight: 600,
              fontSize: 13,
              color: "text.primary",
              letterSpacing: "-0.01em",
            }}
          >
            {headerTitle}
          </Typography>
          {mode === "sidebar" &&
            context.page !== "general" &&
            CONTEXT_LABELS[context.page] && (
              <Chip
                size="small"
                icon={
                  CONTEXT_ICONS[context.page] ? (
                    <Iconify icon={CONTEXT_ICONS[context.page]} width={14} />
                  ) : undefined
                }
                label={CONTEXT_LABELS[context.page]}
                variant="outlined"
                sx={{
                  borderColor: "border.default",
                  color: "text.secondary",
                  "& .MuiChip-icon": {
                    color: "text.secondary",
                    ml: 0.5,
                  },
                }}
              />
            )}
        </Box>

        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          <IconButton
            ref={skillsBtnRef}
            size="small"
            onClick={(e) => setSkillsAnchor(e.currentTarget)}
            title="Skills"
            sx={{
              color: skillsMenuOpen ? "text.primary" : "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
          >
            <Iconify icon="solar:widget-linear" width={20} />
          </IconButton>
          <IconButton
            size="small"
            onClick={handleHistoryOpen}
            title="Chat history"
            sx={{
              color: historyOpen ? "text.primary" : "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
          >
            <Iconify icon="solar:history-linear" width={20} />
          </IconButton>
          <IconButton
            size="small"
            onClick={handleNewChat}
            title="New chat"
            sx={{
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
          >
            <Iconify icon="solar:pen-new-square-linear" width={20} />
          </IconButton>
          {onClose && (
            <IconButton
              size="small"
              onClick={onClose}
              title="Close"
              sx={{
                color: "text.secondary",
                "&:hover": { color: "text.primary" },
              }}
            >
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          )}
        </Box>
      </Box>

      {/* Skills Menu */}
      <Menu
        anchorEl={skillsAnchor}
        open={skillsMenuOpen}
        onClose={() => setSkillsAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 240,
              maxWidth: 320,
              maxHeight: 400,
              mt: 0.5,
              borderRadius: "12px",
              border: 1,
              borderColor: "divider",
            },
          },
        }}
      >
        <Box sx={{ px: 2, py: 1 }}>
          <Typography
            variant="caption"
            sx={{
              color: "text.disabled",
              fontWeight: 600,
              fontSize: 12,
              textTransform: "none",
              letterSpacing: 0,
            }}
          >
            Skills
          </Typography>
        </Box>

        {(!skills || skills.length === 0) && (
          <Box sx={{ px: 2, py: 1.5 }}>
            <Typography sx={{ fontSize: 13, color: "text.disabled" }}>
              No skills configured yet
            </Typography>
          </Box>
        )}

        {(skills || []).map((skill) => (
          <MenuItem
            key={skill.id}
            onClick={() => handleSkillClick(skill)}
            sx={{ py: 1, px: 2, borderRadius: "8px", mx: 0.5 }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify
                icon={skill.icon || "solar:star-linear"}
                width={16}
                sx={{ color: "text.secondary" }}
              />
            </ListItemIcon>
            <ListItemText
              primary={skill.name}
              secondary={skill.description || null}
              primaryTypographyProps={{ fontSize: 13 }}
              secondaryTypographyProps={{ fontSize: 12, noWrap: true }}
            />
          </MenuItem>
        ))}

        <Divider sx={{ my: 0.5 }} />

        <MenuItem
          onClick={handleCreateSkill}
          sx={{
            py: 1,
            px: 2,
            borderRadius: "8px",
            mx: 0.5,
            mb: 0.5,
          }}
        >
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify
              icon="solar:add-linear"
              width={16}
              sx={{ color: "text.disabled" }}
            />
          </ListItemIcon>
          <ListItemText
            primary="Create Skill"
            primaryTypographyProps={{
              fontSize: 13,
              color: "text.secondary",
            }}
          />
        </MenuItem>
      </Menu>

      {/* Chat History Popover */}
      <Popover
        open={historyOpen}
        anchorEl={historyAnchor}
        onClose={() => setHistoryAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 320,
              maxHeight: 420,
              mt: 0.5,
              borderRadius: "12px",
              border: 1,
              borderColor: "divider",
              display: "flex",
              flexDirection: "column",
            },
          },
        }}
      >
        {/* Search */}
        <Box sx={{ p: 1.5, pb: 1 }}>
          <TextField
            size="small"
            fullWidth
            autoFocus
            placeholder="Search chats..."
            value={historySearch}
            onChange={(e) => setHistorySearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{
              "& .MuiInputBase-root": {
                fontSize: 13,
                height: 34,
                borderRadius: "8px",
              },
            }}
          />
        </Box>

        {/* List */}
        <Box
          ref={historyListRef}
          onScroll={handleHistoryScroll}
          sx={{ flex: 1, overflow: "auto", px: 0.75, pb: 1 }}
        >
          {historyItems.length === 0 && !historyLoading && (
            <Typography
              sx={{
                textAlign: "center",
                py: 3,
                color: "text.disabled",
                fontSize: 12,
              }}
            >
              {historySearch ? "No matching chats" : "No chat history"}
            </Typography>
          )}
          {historyItems.map((conv) => (
            <ListItemButton
              key={conv.id}
              selected={conv.id === currentConversationId}
              onClick={() => handleHistorySelect(conv)}
              sx={{
                borderRadius: "6px",
                mb: "1px",
                px: 1.5,
                py: 0,
                height: 28,
                minHeight: 0,
                "&.Mui-selected": { bgcolor: theme.palette.action.selected },
                "&:hover": { bgcolor: theme.palette.action.hover },
              }}
            >
              <ListItemText
                primary={conv.title || "Untitled"}
                primaryTypographyProps={{
                  noWrap: true,
                  fontSize: 13,
                  fontWeight: conv.id === currentConversationId ? 500 : 400,
                }}
              />
            </ListItemButton>
          ))}
          {historyLoading && (
            <Typography
              sx={{
                textAlign: "center",
                py: 1,
                color: "text.disabled",
                fontSize: 12,
              }}
            >
              Loading...
            </Typography>
          )}
        </Box>
      </Popover>

      <SkillEditorDialog
        open={editorOpen}
        skill={null}
        onClose={() => setEditorOpen(false)}
        onSaved={async () => {
          try {
            const { listSkills } = await import("../hooks/useFalconAPI");
            const data = await listSkills();
            const results = data.results || data || [];
            const skillList = Array.isArray(results) ? results : [];
            useFalconStore.getState().setSkills(skillList);
          } catch {
            // silent
          }
        }}
      />
    </>
  );
}

FalconAIHeader.propTypes = {
  onClose: PropTypes.func,
  mode: PropTypes.oneOf(["sidebar", "fullpage"]),
};
