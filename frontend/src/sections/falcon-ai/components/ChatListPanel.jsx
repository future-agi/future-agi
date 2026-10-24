import React, { useEffect, useState, useCallback, useRef } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import PropTypes from "prop-types";
import { useNavigate, useLocation } from "react-router-dom";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import TextField from "@mui/material/TextField";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import InputAdornment from "@mui/material/InputAdornment";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import {
  fetchConversations,
  getConversation,
  deleteConversation,
  renameConversation,
  checkStreamStatus,
} from "../hooks/useFalconAPI";

// ---------------------------------------------------------------------------
// Date grouping
// ---------------------------------------------------------------------------
function getDateGroup(dateStr) {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const sevenDaysAgo = new Date(today);
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
  const thirtyDaysAgo = new Date(today);
  thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

  if (date >= today) return "Today";
  if (date >= yesterday) return "Yesterday";
  if (date >= sevenDaysAgo) return "Previous 7 days";
  if (date >= thirtyDaysAgo) return "Previous 30 days";
  return date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function groupByDate(conversations) {
  const groups = [];
  let currentGroup = null;
  for (const conv of conversations) {
    const group = getDateGroup(conv.updated_at || conv.created_at);
    if (group !== currentGroup) {
      groups.push({ type: "header", label: group });
      currentGroup = group;
    }
    groups.push({ type: "item", conv });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// ConversationItem — isolated for clean hover behavior
// ---------------------------------------------------------------------------
function ConversationItem({ conv, isActive, onSelect, onMenuOpen, theme }) {
  return (
    <Box
      onClick={() => onSelect(conv.id)}
      sx={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        height: 36,
        px: 1.5,
        mx: 0.5,
        borderRadius: "6px",
        cursor: "pointer",
        transition: "background 0.15s ease",
        bgcolor: isActive ? "action.selected" : "transparent",
        "&:hover": {
          bgcolor: "action.hover",
        },
        // Hover: show action button with gradient fade
        "&:hover .conv-actions": {
          opacity: 1,
        },
      }}
    >
      <Typography
        noWrap
        sx={{
          flex: 1,
          fontSize: 13,
          fontWeight: isActive ? 500 : 400,
          color: "text.primary",
          lineHeight: 1.3,
        }}
      >
        {conv.title || "Untitled"}
      </Typography>

      {/* Action button — fades in on hover with gradient mask */}
      <Box
        className="conv-actions"
        sx={{
          position: "absolute",
          right: 0,
          top: 0,
          bottom: 0,
          display: "flex",
          alignItems: "center",
          pr: 0.5,
          pl: 3,
          opacity: 0,
          transition: "opacity 0.15s ease",
          background: `linear-gradient(to right, transparent, ${
            isActive
              ? theme.palette.action.selected
              : theme.palette.action.hover
          } 40%)`,
          borderRadius: "0 6px 6px 0",
        }}
      >
        <IconButton
          size="small"
          onClick={(e) => onMenuOpen(e, conv.id)}
          sx={{
            color: "text.secondary",
            "&:hover": { color: "text.primary" },
          }}
        >
          <Iconify icon="solar:menu-dots-linear" width={16} />
        </IconButton>
      </Box>
    </Box>
  );
}

ConversationItem.propTypes = {
  conv: PropTypes.shape({
    id: PropTypes.string,
    title: PropTypes.string,
  }).isRequired,
  isActive: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
  onMenuOpen: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
const PAGE_SIZE = 30;

export default function ChatListPanel() {
  const theme = useTheme();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const isOnFalconPage = pathname.startsWith("/dashboard/falcon-ai");

  const conversations = useFalconStore((s) => s.conversations);
  const setConversations = useFalconStore((s) => s.setConversations);
  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const setMessages = useFalconStore((s) => s.setMessages);
  const setStreaming = useFalconStore((s) => s.setStreaming);
  const resetChat = useFalconStore((s) => s.resetChat);

  const [search, setSearch] = useState("");
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [menuConvId, setMenuConvId] = useState(null);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const searchTimerRef = useRef(null);
  const loadingRef = useRef(false);
  const scrollRef = useRef(null);

  // ── Fetch ────────────────────────────────────────────────────────────
  const loadConversations = useCallback(
    async (offset = 0, searchQuery = "", append = false) => {
      if (loadingRef.current) return;
      loadingRef.current = true;
      setLoading(true);
      try {
        const data = await fetchConversations({
          limit: PAGE_SIZE,
          offset,
          search: searchQuery,
        });
        const results = data.results || [];
        if (append) {
          const current = useFalconStore.getState().conversations || [];
          const existingIds = new Set(current.map((c) => c.id));
          const newItems = results.filter((r) => !existingIds.has(r.id));
          if (newItems.length > 0) setConversations([...current, ...newItems]);
        } else {
          setConversations(results);
        }
        setHasMore((data.has_more ?? results.length >= PAGE_SIZE) === true);
      } catch {
        /* silent */
      } finally {
        setLoading(false);
        loadingRef.current = false;
      }
    },
    [setConversations],
  );

  useEffect(() => {
    loadConversations(0, "");
  }, [loadConversations]);

  useEffect(() => {
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setHasMore(true);
      loadConversations(0, search);
    }, 300);
    return () => clearTimeout(searchTimerRef.current);
  }, [search, loadConversations]);

  // Scroll-based load more — simple scroll listener
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !hasMore || loadingRef.current) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
      const offset = useFalconStore.getState().conversations.length;
      loadConversations(offset, search, true);
    }
  }, [hasMore, search, loadConversations]);

  // ── Handlers ─────────────────────────────────────────────────────────
  const handleNewChat = useCallback(() => {
    resetChat();
    setCurrentConversation(null);
    useFalconStore.getState().setShowCustomize(false);
    if (isOnFalconPage) navigate("/dashboard/falcon-ai", { replace: true });
  }, [resetChat, setCurrentConversation, isOnFalconPage, navigate]);

  const handleSelect = useCallback(
    async (id) => {
      setCurrentConversation(id);
      useFalconStore.getState().setShowCustomize(false);
      if (isOnFalconPage)
        navigate(`/dashboard/falcon-ai/${id}`, { replace: true });
      try {
        const data = await getConversation(id);
        const conv = data.result || data;
        // Normalize camelCase API response to snake_case for components
        const msgs = (conv.messages || []).map((m) => ({
          ...m,
          tool_calls: m.tool_calls || [],
          completion_card: m.completion_card || null,
          created_at: m.created_at,
        }));
        setMessages(msgs);
        try {
          const s = await checkStreamStatus(id);
          if (["running", "done"].includes(s.result?.stream_status))
            setStreaming(true, null);
        } catch {
          /* non-critical */
        }
      } catch {
        /* failed */
      }
    },
    [
      setCurrentConversation,
      setMessages,
      setStreaming,
      isOnFalconPage,
      navigate,
    ],
  );

  const handleMenuOpen = (e, id) => {
    e.stopPropagation();
    setMenuAnchor(e.currentTarget);
    setMenuConvId(id);
  };
  const handleMenuClose = () => {
    setMenuAnchor(null);
    setMenuConvId(null);
  };

  const handleDelete = async () => {
    if (!menuConvId) return;
    try {
      await deleteConversation(menuConvId);
      setConversations(conversations.filter((c) => c.id !== menuConvId));
      if (currentConversationId === menuConvId) handleNewChat();
    } catch {
      /* silent */
    }
    handleMenuClose();
  };

  const handleRename = async () => {
    if (!menuConvId) return;
    const conv = conversations.find((c) => c.id === menuConvId);
    const newTitle = window.prompt("Rename conversation", conv?.title || "");
    if (newTitle && newTitle !== conv?.title) {
      try {
        await renameConversation(menuConvId, newTitle);
        setConversations(
          conversations.map((c) =>
            c.id === menuConvId ? { ...c, title: newTitle } : c,
          ),
        );
      } catch {
        /* silent */
      }
    }
    handleMenuClose();
  };

  const grouped = search
    ? conversations.map((c) => ({ type: "item", conv: c }))
    : groupByDate(conversations);

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <Box
      sx={{
        width: 224,
        minWidth: 224,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid",
        borderColor: "border.default",
        bgcolor: "background.paper",
        height: "100%",
      }}
    >
      {/* ── Top section: New Chat + Customize ──────────────────────── */}
      <Box
        sx={{
          px: 1,
          pt: 1.5,
          pb: 0.5,
          display: "flex",
          flexDirection: "column",
          gap: 0.25,
        }}
      >
        <ButtonBase
          component="div"
          onClick={handleNewChat}
          sx={{
            justifyContent: "flex-start",
            textAlign: "left",
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.5,
            height: 36,
            borderRadius: "6px",
            cursor: "pointer",
            color: "text.primary",
            "&:hover": {
              bgcolor: "action.hover",
            },
          }}
        >
          <Iconify icon="solar:add-linear" width={16} />
          <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
            New chat
          </Typography>
        </ButtonBase>

        <ButtonBase
          component="div"
          onClick={() => useFalconStore.getState().setShowCustomize(true)}
          sx={{
            justifyContent: "flex-start",
            textAlign: "left",
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.5,
            height: 36,
            borderRadius: "6px",
            cursor: "pointer",
            color: "text.secondary",
            "&:hover": {
              bgcolor: "action.hover",
              color: "text.primary",
            },
          }}
        >
          <Iconify icon="solar:tuning-2-linear" width={16} />
          <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
            Customize
          </Typography>
        </ButtonBase>
      </Box>

      {/* ── Search ─────────────────────────────────────────────────── */}
      <Box sx={{ px: 1, pb: 0.75 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search chats..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
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
            ...(search && {
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setSearch("")}>
                    <Iconify icon="solar:close-linear" width={14} />
                  </IconButton>
                </InputAdornment>
              ),
            }),
          }}
          sx={{
            "& .MuiInputBase-root": {
              fontSize: 13,
              height: 36,
              borderRadius: "8px",
              bgcolor: "transparent",
            },
            "& .MuiOutlinedInput-notchedOutline": {
              borderColor: "transparent",
            },
            "& .MuiInputBase-root:hover .MuiOutlinedInput-notchedOutline": {
              borderColor: "border.default",
            },
            "& .MuiInputBase-root.Mui-focused .MuiOutlinedInput-notchedOutline":
              {
                borderColor: theme.palette.primary.main,
                borderWidth: 1.5,
              },
          }}
        />
      </Box>

      {/* ── Conversation list ──────────────────────────────────────── */}
      <Box
        ref={scrollRef}
        onScroll={handleScroll}
        sx={{
          flex: "1 1 0",
          minHeight: 0,
          overflow: "auto",
          pb: 1,
          // Slim scrollbar
          "&::-webkit-scrollbar": { width: 4 },
          "&::-webkit-scrollbar-thumb": {
            bgcolor: "action.hover",
            borderRadius: 2,
          },
          "&::-webkit-scrollbar-track": { bgcolor: "transparent" },
        }}
      >
        {grouped.map((entry, idx) => {
          if (entry.type === "header") {
            return (
              <Typography
                key={`h-${entry.label}`}
                sx={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.tertiary",
                  px: 2,
                  pt: idx === 0 ? 0.5 : 2,
                  pb: 0.5,
                  letterSpacing: 0.3,
                }}
              >
                {entry.label}
              </Typography>
            );
          }

          return (
            <ConversationItem
              key={entry.conv.id}
              conv={entry.conv}
              isActive={entry.conv.id === currentConversationId}
              onSelect={handleSelect}
              onMenuOpen={handleMenuOpen}
              theme={theme}
            />
          );
        })}

        {conversations.length === 0 && !loading && (
          <Typography
            sx={{
              textAlign: "center",
              py: 4,
              color: "text.disabled",
              fontSize: 13,
            }}
          >
            {search ? "No matching chats" : "No conversations yet"}
          </Typography>
        )}

        {/* Loading spinner */}
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={18} sx={{ color: "text.disabled" }} />
          </Box>
        )}
      </Box>

      {/* ── Context menu ───────────────────────────────────────────── */}
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={handleMenuClose}
        PaperProps={{
          sx: {
            minWidth: 200,
            borderRadius: "8px",
          },
        }}
      >
        <MenuItem
          onClick={handleRename}
          sx={{ py: 0.75, gap: 1.5 }}
        >
          <Iconify
            icon="solar:pen-linear"
            width={16}
            sx={{ color: "text.secondary" }}
          />
          Rename
        </MenuItem>
        <MenuItem
          onClick={handleDelete}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ py: 0.75, gap: 1.5, color: "status.danger.fg" }}
        >
          <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          Delete
        </MenuItem>
      </Menu>
    </Box>
  );
}
