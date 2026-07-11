import React, {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
} from "react";
import Chip from "@mui/material/Chip";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemText from "@mui/material/ListItemText";
import Switch from "@mui/material/Switch";
import Divider from "@mui/material/Divider";
import CircularProgress from "@mui/material/CircularProgress";
import { alpha, useTheme } from "@mui/material/styles";
import { useRouter } from "src/routes/hooks";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import { uploadFile } from "../hooks/useFalconAPI";
import ContextSelector from "./ContextSelector";
import SlashCommandPicker from "./SlashCommandPicker";
import AttachedFileChip from "./AttachedFileChip";
const MAX_LENGTH = 10000;
const MAX_FILE_BYTES = 10 * 1024 * 1024; // mirrors backend views.py MAX_FILE_SIZE
const MAX_DROPPED_FILES = 5;

export default function ChatInput({ onSend, onStop }) {
  const [text, setText] = useState("");
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  // Inline notice for silently-impossible inputs (oversized files, dropped
  // extras). These used to fail with NO feedback — an accuracy-of-presentation
  // bug: the user believed the file was attached when it wasn't.
  const [inputNotice, setInputNotice] = useState(null);
  const theme = useTheme();
  const router = useRouter();
  const inputContainerRef = useRef(null);
  const fileInputRef = useRef(null);
  const pickerRef = useRef(null);
  const textFieldRef = useRef(null);
  const isStreaming = useFalconStore((s) => s.isStreaming);
  const resetChat = useFalconStore((s) => s.resetChat);
  const setCurrentConversation = useFalconStore(
    (s) => s.setCurrentConversation,
  );
  const triggerSkillsMenu = useFalconStore((s) => s.triggerSkillsMenu);
  const currentConversationId = useFalconStore((s) => s.currentConversationId);
  const attachedFiles = useFalconStore((s) => s.attachedFiles);
  const addAttachedFile = useFalconStore((s) => s.addAttachedFile);
  const removeAttachedFile = useFalconStore((s) => s.removeAttachedFile);
  const skills = useFalconStore((s) => s.skills);
  const pendingPrompt = useFalconStore((s) => s.pendingPrompt);
  const clearPendingPrompt = useFalconStore((s) => s.clearPendingPrompt);

  // Reset dismissed flag when text changes so picker can reappear
  useEffect(() => {
    setSlashDismissed(false);
  }, [text]);

  // Hydrate from a pending prompt set by external callers (e.g. "Fix with
  // Falcon" buttons or the empty-state quick actions). Prefill only — user
  // still presses send. Consume once to avoid re-hydrating on every re-render.
  // After prefill, focus the textarea, scroll it to the top so the start of
  // the prompt is visible, and place the caret at the end so the user can
  // immediately edit/append.
  useEffect(() => {
    if (!pendingPrompt) return;
    setText(pendingPrompt);
    clearPendingPrompt();
    requestAnimationFrame(() => {
      const ta = textFieldRef.current?.querySelector("textarea");
      if (!ta) return;
      ta.focus();
      ta.scrollTop = 0;
      const end = pendingPrompt.length;
      ta.setSelectionRange(end, end);
    });
  }, [pendingPrompt, clearPendingPrompt]);

  // Detect skill slugs in text for inline chip display
  const detectedSkills = useMemo(() => {
    if (!text || !skills?.length) return [];
    const matches = text.match(/\/([a-z0-9-]+)/gi) || [];
    return matches
      .map((m) => skills.find((s) => s.slug === m.slice(1)))
      .filter(Boolean);
  }, [text, skills]);

  // Reserve a comfortable preview window for prefilled prompts so the user can
  // read what was inserted without scrolling immediately. Empty / short input
  // stays as a single row so the empty-state UI doesn't feel bloated.
  const computedMinRows = useMemo(() => {
    if (!text) return 1;
    if (text.length > 240) return 6;
    if (text.length > 120) return 4;
    if (text.length > 60) return 2;
    return 1;
  }, [text]);

  // While the assistant is streaming the textarea is disabled (see
  // `disabled={isStreaming}` below). Reflect that in the placeholder so the
  // field doesn't keep inviting input with no signal it's locked (TH-5232).
  const placeholder = isStreaming
    ? "Falcon is responding…"
    : currentConversationId
      ? "Ask a follow-up..."
      : "Message Falcon AI...";

  // Over-limit messages are BLOCKED (with a visible explanation), never
  // silently sliced — the old `.slice(0, MAX_LENGTH)` cut user text without
  // any signal, so the model answered a different question than the user
  // believed they asked.
  const overLimit = text.length > MAX_LENGTH;
  const nearLimit = text.length >= MAX_LENGTH * 0.9;

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    const hasFiles = useFalconStore.getState().attachedFiles.length > 0;
    if (!trimmed && !hasFiles) return;
    if (text.length > MAX_LENGTH) return; // blocked — counter explains why
    onSend?.(trimmed || "See attached files");
    setText("");
  }, [text, onSend]);

  const handleFileSelect = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      // Reset input so same file can be re-selected
      e.target.value = "";

      if (file.size > MAX_FILE_BYTES) {
        setInputNotice(
          `"${file.name}" is larger than 10 MB and was not attached.`,
        );
        return;
      }

      setInputNotice(null);
      setIsUploading(true);
      try {
        const resp = await uploadFile(file);
        if (resp.status && resp.result) {
          addAttachedFile(resp.result);
        }
      } catch {
        // silent — upload error handled by UI state
      } finally {
        setIsUploading(false);
      }
    },
    [addAttachedFile],
  );

  const handleKeyDown = (e) => {
    // Delegate to slash command picker first (arrow keys, enter, escape)
    if (pickerRef.current?.handleKeyDown?.(e)) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSlashSelect = useCallback(
    (cmd) => {
      if (!cmd) {
        // Escape pressed — hide picker without clearing text
        setSlashDismissed(true);
        return;
      }

      // Handle skill commands — insert slash command inline, user types after it
      if (cmd.type === "skill" && cmd.skill) {
        setText(`/${cmd.skill.slug} `);
        return;
      }

      setText("");

      // Handle system commands
      if (cmd.command === "/clear") {
        resetChat();
        setCurrentConversation(null);
        // Drop the conversation id from the URL too — otherwise a refresh
        // re-hydrates the cleared conversation from the route param.
        // (window.location, not a hook: ChatInput also renders in the
        // sidebar on arbitrary pages, where no navigation should happen.)
        if (window.location.pathname.startsWith("/dashboard/falcon-ai")) {
          router.replace("/dashboard/falcon-ai");
        }
      }
    },
    [onSend, triggerSkillsMenu, resetChat, setCurrentConversation, router],
  );

  const isDark = theme.palette.mode === "dark";
  const connectors = useFalconStore((s) => s.connectors);
  const [plusMenuAnchor, setPlusMenuAnchor] = useState(null);
  const [connectorsSubmenu, setConnectorsSubmenu] = useState(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    async (e) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer?.files || []);
      const sized = files.filter((f) => f.size <= MAX_FILE_BYTES);
      const validFiles = sized.slice(0, MAX_DROPPED_FILES);

      // Surface every silently-dropped file — oversized AND over-count.
      const notices = [];
      const oversized = files.filter((f) => f.size > MAX_FILE_BYTES);
      if (oversized.length > 0) {
        notices.push(
          `${oversized.map((f) => `"${f.name}"`).join(", ")} ${
            oversized.length === 1 ? "is" : "are"
          } larger than 10 MB and ${
            oversized.length === 1 ? "was" : "were"
          } not attached.`,
        );
      }
      if (sized.length > MAX_DROPPED_FILES) {
        notices.push(
          `Only the first ${MAX_DROPPED_FILES} files were attached (${
            sized.length - MAX_DROPPED_FILES
          } skipped).`,
        );
      }
      setInputNotice(notices.length > 0 ? notices.join(" ") : null);
      if (validFiles.length === 0) return;

      setIsUploading(true);
      try {
        const results = await Promise.allSettled(
          validFiles.map((file) => uploadFile(file)),
        );
        for (const result of results) {
          if (
            result.status === "fulfilled" &&
            result.value.status &&
            result.value.result
          ) {
            addAttachedFile(result.value.result);
          }
        }
      } catch {
        // silent — upload error handled by UI state
      } finally {
        setIsUploading(false);
      }
    },
    [addAttachedFile],
  );

  const handlePlusClick = (e) => setPlusMenuAnchor(e.currentTarget);
  const handlePlusClose = () => {
    setPlusMenuAnchor(null);
    setConnectorsSubmenu(null);
  };
  const handleConnectorsHover = (e) => setConnectorsSubmenu(e.currentTarget);

  return (
    <Box
      sx={{
        px: 2,
        pb: 0.5,
        pt: 1,
        maxWidth: 800,
        width: "100%",
        mx: "auto",
      }}
    >
      <Box
        ref={inputContainerRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        sx={{
          position: "relative",
          border: isDragging ? 2 : 1,
          borderColor: isDragging
            ? "primary.main"
            : isDark
              ? alpha(theme.palette.common.white, 0.12)
              : alpha(theme.palette.common.black, 0.12),
          borderStyle: isDragging ? "dashed" : "solid",
          borderRadius: "20px",
          overflow: "visible",
          bgcolor: isDragging
            ? alpha(theme.palette.primary.main, 0.04)
            : isDark
              ? alpha(theme.palette.common.white, 0.04)
              : theme.palette.background.paper,
          boxShadow: isDark
            ? `0 0 0 1px ${alpha(theme.palette.common.white, 0.06)}`
            : `0 2px 12px ${alpha(theme.palette.common.black, 0.06)}`,
          transition: "border-color 0.2s ease, box-shadow 0.2s ease",
          "&:focus-within": {
            borderColor: isDark
              ? alpha(theme.palette.common.white, 0.24)
              : alpha(theme.palette.primary.main, 0.4),
            boxShadow: isDark
              ? `0 0 0 1px ${alpha(theme.palette.common.white, 0.12)}`
              : `0 2px 16px ${alpha(theme.palette.primary.main, 0.08)}`,
          },
        }}
      >
        <SlashCommandPicker
          inputText={slashDismissed ? "" : text}
          onSelect={handleSlashSelect}
          anchorRef={inputContainerRef}
          pickerRef={pickerRef}
        />

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          hidden
          accept=".txt,.csv,.html,.md,.json,.pdf,.xlsx,.docx,.png,.jpg,.jpeg,.gif,.webp"
          onChange={handleFileSelect}
        />

        {/* Attached files chips */}
        {attachedFiles.length > 0 && (
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 0.75,
              px: 2,
              pt: 1.5,
              pb: 0,
            }}
          >
            {attachedFiles.map((f) => (
              <AttachedFileChip
                key={f.id}
                file={f}
                onRemove={removeAttachedFile}
              />
            ))}
            {isUploading && (
              <Box
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.75,
                  px: 1.25,
                  py: 0.5,
                }}
              >
                <CircularProgress size={14} />
                <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
                  Uploading...
                </Typography>
              </Box>
            )}
          </Box>
        )}

        {/* Inline input notice — files rejected/skipped are never silent */}
        {inputNotice && (
          <Box
            role="status"
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: 0.75,
              px: 2,
              pt: 1.25,
              pb: 0,
            }}
          >
            <Iconify
              icon="mdi:alert-circle-outline"
              width={14}
              sx={{ color: "warning.main", flexShrink: 0, mt: "1px" }}
            />
            <Typography
              sx={{ fontSize: 12, color: "text.secondary", lineHeight: 1.5 }}
            >
              {inputNotice}
            </Typography>
            <IconButton
              size="small"
              title="Dismiss notice"
              onClick={() => setInputNotice(null)}
              sx={{ p: 0.25, ml: "auto", color: "text.disabled" }}
            >
              <Iconify icon="mdi:close" width={12} />
            </IconButton>
          </Box>
        )}

        {/* Detected skill chips — shown when user types /skill-slug */}
        {detectedSkills.length > 0 && (
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 0.5,
              px: 2,
              pt: 1.5,
              pb: 0,
            }}
          >
            {detectedSkills.map((s) => (
              <Chip
                key={s.slug}
                icon={
                  <Iconify icon={s.icon || "mdi:lightning-bolt"} width={13} />
                }
                label={s.name}
                size="small"
                onDelete={() => {
                  // Remove the /slug from text
                  setText((prev) =>
                    prev.replace(`/${s.slug}`, "").replace(/\s+/g, " ").trim(),
                  );
                }}
                sx={{
                  height: 24,
                  fontSize: 11,
                  fontWeight: 600,
                  bgcolor: alpha(theme.palette.primary.main, 0.12),
                  color: "primary.main",
                  "& .MuiChip-icon": { color: "primary.main" },
                  "& .MuiChip-deleteIcon": {
                    color: alpha(theme.palette.primary.main, 0.4),
                    fontSize: 14,
                    "&:hover": { color: "primary.main" },
                  },
                }}
              />
            ))}
          </Box>
        )}

        <TextField
          fullWidth
          multiline
          minRows={computedMinRows}
          maxRows={12}
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
          variant="standard"
          ref={textFieldRef}
          InputProps={{
            disableUnderline: true,
          }}
          sx={{
            px: 2,
            pt: 1.5,
            pb: 0.5,
            "& .MuiInputBase-input": {
              fontSize: 14,
              lineHeight: 1.6,
              color: "text.primary",
              "&::placeholder": {
                color: "text.disabled",
                opacity: 1,
              },
            },
            "& textarea": {
              overflowY: "auto",
            },
          }}
        />

        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            px: 1,
            pb: 0.75,
            pt: 0,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <ContextSelector />
            <IconButton
              size="small"
              title="Attach file"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
              sx={{
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
            >
              <Iconify
                icon={isUploading ? "mdi:loading" : "mdi:paperclip"}
                width={18}
                sx={
                  isUploading
                    ? {
                        animation: "spin 1s linear infinite",
                        "@keyframes spin": {
                          "100%": { transform: "rotate(360deg)" },
                        },
                      }
                    : {}
                }
              />
            </IconButton>
            <IconButton
              size="small"
              title="More"
              onClick={handlePlusClick}
              sx={{
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
            >
              <Iconify icon="mdi:plus" width={18} />
            </IconButton>

            {/* Plus menu */}
            <Menu
              anchorEl={plusMenuAnchor}
              open={Boolean(plusMenuAnchor)}
              onClose={handlePlusClose}
              anchorOrigin={{ vertical: "top", horizontal: "left" }}
              transformOrigin={{ vertical: "bottom", horizontal: "left" }}
              slotProps={{
                paper: {
                  sx: {
                    borderRadius: "12px",
                    minWidth: 220,
                    boxShadow: isDark
                      ? `0 8px 24px ${alpha(theme.palette.common.black, 0.4)}`
                      : `0 8px 24px ${alpha(theme.palette.common.black, 0.12)}`,
                  },
                },
              }}
            >
              <MenuItem
                onClick={() => {
                  handlePlusClose();
                  // Delay click to ensure menu closes first
                  setTimeout(() => fileInputRef.current?.click(), 100);
                }}
                disabled={isUploading}
                sx={{ gap: 1.5, py: 1 }}
              >
                <Iconify
                  icon="mdi:paperclip"
                  width={18}
                  sx={{ color: "text.secondary" }}
                />
                <ListItemText
                  primary={isUploading ? "Uploading..." : "Attach files"}
                  primaryTypographyProps={{ fontSize: 14 }}
                />
              </MenuItem>

              <MenuItem
                onMouseEnter={handleConnectorsHover}
                sx={{ gap: 1.5, py: 1, justifyContent: "space-between" }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                  <Iconify
                    icon="mdi:puzzle-outline"
                    width={18}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography sx={{ fontSize: 14 }}>Connectors</Typography>
                </Box>
                <Iconify
                  icon="mdi:chevron-right"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </MenuItem>
            </Menu>

            {/* Connectors submenu */}
            <Menu
              anchorEl={connectorsSubmenu}
              open={Boolean(connectorsSubmenu) && Boolean(plusMenuAnchor)}
              onClose={() => setConnectorsSubmenu(null)}
              anchorOrigin={{ vertical: "top", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "left" }}
              slotProps={{
                paper: {
                  sx: {
                    borderRadius: "12px",
                    minWidth: 240,
                    ml: 0.5,
                    boxShadow: isDark
                      ? `0 8px 24px ${alpha(theme.palette.common.black, 0.4)}`
                      : `0 8px 24px ${alpha(theme.palette.common.black, 0.12)}`,
                  },
                },
              }}
            >
              {(connectors || []).length > 0 ? (
                (connectors || []).map((c) => (
                  <MenuItem
                    key={c.id}
                    sx={{ gap: 1.5, py: 0.75, justifyContent: "space-between" }}
                  >
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Box
                        sx={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          bgcolor: c.is_verified
                            ? "success.main"
                            : "text.disabled",
                        }}
                      />
                      <Typography sx={{ fontSize: 13 }}>{c.name}</Typography>
                    </Box>
                    <Switch size="small" checked={c.is_active !== false} />
                  </MenuItem>
                ))
              ) : (
                <MenuItem disabled sx={{ py: 1 }}>
                  <Typography
                    sx={{
                      fontSize: 13,
                      fontStyle: "italic",
                      color: "text.disabled",
                    }}
                  >
                    No connectors configured
                  </Typography>
                </MenuItem>
              )}
              <Divider />
              <MenuItem
                onClick={() => {
                  handlePlusClose();
                  router.push("/dashboard/settings/falcon-ai-connectors");
                }}
                sx={{ gap: 1.5, py: 1 }}
              >
                <Iconify
                  icon="mdi:cog-outline"
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
                <Typography sx={{ fontSize: 13 }}>Manage connectors</Typography>
              </MenuItem>
            </Menu>
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            {/* Character budget — visible from 90%; over-limit blocks send
                with an explanation instead of silently truncating */}
            {nearLimit && (
              <Typography
                component="span"
                role="status"
                sx={{
                  fontSize: 11,
                  fontVariantNumeric: "tabular-nums",
                  color: overLimit ? "error.main" : "text.disabled",
                  fontWeight: overLimit ? 600 : 400,
                  whiteSpace: "nowrap",
                }}
              >
                {overLimit
                  ? `Message too long — ${text.length.toLocaleString()} / ${MAX_LENGTH.toLocaleString()} characters. Shorten it or attach it as a file.`
                  : `${text.length.toLocaleString()} / ${MAX_LENGTH.toLocaleString()}`}
              </Typography>
            )}

            {isStreaming ? (
              <IconButton
                size="small"
                onClick={onStop}
                title="Stop"
                sx={{
                  bgcolor: "error.main",
                  color: "error.contrastText",
                  width: 32,
                  height: 32,
                  "&:hover": {
                    bgcolor: "error.dark",
                  },
                }}
              >
                <Iconify icon="mdi:stop" width={16} />
              </IconButton>
            ) : (
              <IconButton
                size="small"
                onClick={handleSend}
                disabled={
                  (!text.trim() && attachedFiles.length === 0) || overLimit
                }
                title="Send"
                sx={{
                  bgcolor:
                    (text.trim() || attachedFiles.length > 0) && !overLimit
                      ? "text.primary"
                      : "transparent",
                  color:
                    (text.trim() || attachedFiles.length > 0) && !overLimit
                      ? isDark
                        ? "common.black"
                        : "common.white"
                      : "text.disabled",
                  width: 32,
                  height: 32,
                  transition: "all 0.15s ease",
                  "&:hover": {
                    bgcolor:
                      (text.trim() || attachedFiles.length > 0) && !overLimit
                        ? isDark
                          ? "grey.300"
                          : "grey.800"
                        : "transparent",
                  },
                  "&.Mui-disabled": {
                    color: "text.disabled",
                  },
                }}
              >
                <Iconify icon="mdi:arrow-up" width={18} />
              </IconButton>
            )}
          </Box>
        </Box>
      </Box>

      <Typography
        variant="caption"
        sx={{
          display: "block",
          textAlign: "center",
          mt: 0.5,
          mb: 0.5,
          fontSize: 11,
          color: "text.disabled",
          userSelect: "none",
        }}
      >
        Falcon AI can make mistakes. Check important info.
      </Typography>
    </Box>
  );
}

ChatInput.propTypes = {
  onSend: PropTypes.func,
  onStop: PropTypes.func,
};
