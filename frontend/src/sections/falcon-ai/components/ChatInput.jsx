import React, {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
  useId,
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
import Divider from "@mui/material/Divider";
import Switch from "@mui/material/Switch";
import Tooltip from "@mui/material/Tooltip";
import CircularProgress from "@mui/material/CircularProgress";
import { alpha, useTheme } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import { useRouter } from "src/routes/hooks";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import { captureFalconRequest } from "../store/falconRequestGuard";
import { uploadFile } from "../hooks/useFalconAPI";
import ContextSelector from "./ContextSelector";
import SlashCommandPicker from "./SlashCommandPicker";
import AttachedFileChip from "./AttachedFileChip";
import { isPrototypeMode } from "src/utils/prototype";

// Largest file that can be attached (10 MB).
const MAX_FILE_BYTES = 10 * 1024 * 1024;

// Most files one drop attaches.
const MAX_DROPPED_FILES = 5;

// File types Falcon can read. The file picker and drag-and-drop use the same
// list, so a dropped file is checked just like a picked one.
const ACCEPTED_EXTENSIONS = [
  ".txt",
  ".csv",
  ".html",
  ".md",
  ".json",
  ".pdf",
  ".xlsx",
  ".docx",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
];

function isAcceptedType(file) {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

// Why a file over the limit is not uploaded.
const TOO_LARGE_REASON = "Files must be 10 MB or smaller.";

// The sentence that says which files weren't attached, and why.
function notAttachedMessage(names, reason) {
  if (names.length === 0) return null;
  const verb = names.length === 1 ? "wasn't" : "weren't";
  return `${names.join(", ")} ${verb} attached. ${reason}`;
}

// Why an upload didn't attach, in the server's words when it gave any.
function uploadFailureReason(failure) {
  const message =
    typeof failure?.message === "string" ? failure.message.trim() : "";
  if (!message) return "The upload didn't finish. Try again.";
  return /[.!?]$/.test(message) ? message : `${message}.`;
}

// Why a connector's on/off switch here can't be used.
const NOT_IN_RELEASE = "Not available in this release";

// A connector's state in the words the Customize panel uses. The on/off
// setting is shared by the whole workspace, so it is shown here, not changed:
// its switch is turned off, and clicking a connector opens the connectors
// settings page.
function connectorStatus(connector) {
  const isActive = connector.is_active ?? true;
  const isVerified = connector.is_verified ?? false;
  if (!isActive) return { label: "Inactive", color: "text.disabled" };
  if (!isVerified) return { label: "Pending", color: "warning.main" };
  return { label: "Connected", color: "success.main" };
}

export default function ChatInput({ onSend, onStop }) {
  const [text, setText] = useState("");
  const [slashDismissed, setSlashDismissed] = useState(false);
  // Uploads still running. A second drop can start before the first one
  // ends, so this is a count, not a flag.
  const [uploadsInFlight, setUploadsInFlight] = useState(0);
  const isUploading = uploadsInFlight > 0;
  // Why files from the last attach or drop weren't attached. Shown in the
  // message box as well as in a toast: this box often sits in a drawer, and
  // the reason stays next to it after the toast has gone.
  const [notAttached, setNotAttached] = useState([]);
  const theme = useTheme();
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

  const placeholder = currentConversationId
    ? "Ask a follow-up..."
    : "Message Falcon AI...";

  const MAX_LENGTH = 10000;

  // Say which files weren't attached, and why: in a toast and in the box.
  const sayNotAttached = useCallback((names, reason) => {
    const message = notAttachedMessage(names, reason);
    if (!message) return;
    enqueueSnackbar(message, { variant: "error" });
    setNotAttached((shown) => [...shown, message]);
  }, []);

  const handleSend = useCallback(() => {
    // Wait for uploads to finish. Sent now, the message would go without the
    // file, and starting a new chat would drop the upload's result unseen.
    if (isUploading) return;
    const trimmed = text.trim();
    const hasFiles = useFalconStore.getState().attachedFiles.length > 0;
    if (!trimmed && !hasFiles) return;
    onSend?.(trimmed.slice(0, MAX_LENGTH) || "See attached files");
    setText("");
  }, [text, onSend, isUploading]);

  const handleFileSelect = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      // Reset input so same file can be re-selected
      e.target.value = "";
      setNotAttached([]);

      if (file.size > MAX_FILE_BYTES) {
        sayNotAttached([file.name], TOO_LARGE_REASON);
        return;
      }

      const isCurrent = captureFalconRequest({ conversation: true });
      setUploadsInFlight((n) => n + 1);
      try {
        const resp = await uploadFile(file);
        if (!isCurrent()) return;
        if (resp?.status && resp?.result) {
          addAttachedFile(resp.result);
        } else {
          sayNotAttached([file.name], uploadFailureReason(resp));
        }
      } catch (err) {
        if (isCurrent()) {
          sayNotAttached([file.name], uploadFailureReason(err));
        }
      } finally {
        setUploadsInFlight((n) => n - 1);
      }
    },
    [addAttachedFile, sayNotAttached],
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
      }
    },
    [onSend, triggerSkillsMenu, resetChat, setCurrentConversation],
  );

  const router = useRouter();
  const connectors = useFalconStore((s) => s.connectors);
  const [plusMenuAnchor, setPlusMenuAnchor] = useState(null);
  const [connectorsSubmenu, setConnectorsSubmenu] = useState(null);
  const switchReasonId = useId();
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
      if (files.length === 0) return;
      setNotAttached([]);
      sayNotAttached(
        files.filter((f) => f.size > MAX_FILE_BYTES).map((f) => f.name),
        TOO_LARGE_REASON,
      );
      const fitting = files.filter((f) => f.size <= MAX_FILE_BYTES);
      sayNotAttached(
        fitting.filter((f) => !isAcceptedType(f)).map((f) => f.name),
        `Supported file types: ${ACCEPTED_EXTENSIONS.join(", ")}.`,
      );
      const readable = fitting.filter(isAcceptedType);
      sayNotAttached(
        readable.slice(MAX_DROPPED_FILES).map((f) => f.name),
        `You can drop up to ${MAX_DROPPED_FILES} files at a time.`,
      );
      const validFiles = readable.slice(0, MAX_DROPPED_FILES);
      if (validFiles.length === 0) return;

      const isCurrent = captureFalconRequest({ conversation: true });
      setUploadsInFlight((n) => n + 1);
      try {
        const results = await Promise.allSettled(
          validFiles.map((file) => uploadFile(file)),
        );
        if (!isCurrent()) return;
        // Files that failed for the same reason share one notice.
        const failedByReason = new Map();
        results.forEach((result, i) => {
          const resp = result.status === "fulfilled" ? result.value : null;
          if (resp?.status && resp?.result) {
            addAttachedFile(resp.result);
            return;
          }
          const reason = uploadFailureReason(resp ?? result.reason);
          failedByReason.set(reason, [
            ...(failedByReason.get(reason) || []),
            validFiles[i].name,
          ]);
        });
        failedByReason.forEach((names, reason) =>
          sayNotAttached(names, reason),
        );
      } finally {
        setUploadsInFlight((n) => n - 1);
      }
    },
    [addAttachedFile, sayNotAttached],
  );

  const handlePlusClick = (e) => setPlusMenuAnchor(e.currentTarget);
  const handlePlusClose = () => {
    setPlusMenuAnchor(null);
    setConnectorsSubmenu(null);
  };
  const handleConnectorsHover = (e) => setConnectorsSubmenu(e.currentTarget);
  const openConnectorSettings = () => {
    handlePlusClose();
    router.push("/dashboard/settings/falcon-ai-connectors");
  };

  const hasDraft = Boolean(text.trim()) || attachedFiles.length > 0;
  // Send waits for uploads; the "Uploading..." line says why.
  const canSend = hasDraft && !isUploading;

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
          borderColor: isDragging ? "primary.main" : "border.default",
          borderStyle: isDragging ? "dashed" : "solid",
          borderRadius: "12px",
          overflow: "visible",
          bgcolor: isDragging
            ? alpha(theme.palette.primary.main, 0.04)
            : "background.paper",
          transition: "border-color 0.2s ease",
          "&:focus-within": {
            borderColor: "border.hover",
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
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={handleFileSelect}
        />

        {/* Attached files chips, and the upload in progress (shown even
            before the first file is attached) */}
        {(attachedFiles.length > 0 || isUploading) && (
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

        {/* Which files weren't attached, and why. Kept until the next attach
            or drop, or until dismissed. */}
        {notAttached.length > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: 0.5,
              px: 2,
              pt: 1,
            }}
          >
            <Box role="alert" sx={{ flex: 1, minWidth: 0 }}>
              {notAttached.map((message, i) => (
                <Typography
                  key={i}
                  sx={{
                    fontSize: 12,
                    lineHeight: 1.5,
                    color: "error.main",
                    overflowWrap: "anywhere",
                  }}
                >
                  {message}
                </Typography>
              ))}
            </Box>
            <IconButton
              size="small"
              title="Dismiss"
              aria-label="Dismiss"
              onClick={() => setNotAttached([])}
              sx={{
                p: 0.25,
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
            >
              <Iconify icon="solar:close-linear" width={14} />
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
                  <Iconify icon={s.icon || "solar:bolt-linear"} width={14} />
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
                  bgcolor: alpha(theme.palette.primary.main, 0.12),
                  color: "primary.main",
                  "& .MuiChip-icon": { color: "primary.main" },
                  "& .MuiChip-deleteIcon": {
                    color: alpha(theme.palette.primary.main, 0.4),
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
              fontSize: 13,
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
                icon={isUploading ? "mdi:loading" : "solar:paperclip-linear"}
                width={20}
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
              <Iconify icon="solar:add-linear" width={20} />
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
                    minWidth: 240,
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
                  icon="solar:paperclip-linear"
                  width={20}
                  sx={{ color: "text.secondary" }}
                />
                <ListItemText
                  primary={isUploading ? "Uploading..." : "Attach files"}
                  primaryTypographyProps={{ fontSize: 13 }}
                />
              </MenuItem>

              {/* L7c g1 (settings:pages): no connector can be added in the
                  prototype (Falcon AI Connectors is absent there). */}
              {isPrototypeMode() ? null : (
                <MenuItem
                  onMouseEnter={handleConnectorsHover}
                  sx={{ gap: 1.5, py: 1, justifyContent: "space-between" }}
                >
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                    <Iconify
                      icon="solar:widget-linear"
                      width={16}
                      sx={{ color: "text.secondary" }}
                    />
                    <Typography sx={{ fontSize: 13 }}>Connectors</Typography>
                  </Box>
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </MenuItem>
              )}
            </Menu>

            {/* What each connector's turned-off switch says to screen
                readers; the switch shows the same line on hover. */}
            <span id={switchReasonId} hidden>
              {NOT_IN_RELEASE}
            </span>

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
                  },
                },
              }}
            >
              {(connectors || []).length > 0 ? (
                (connectors || []).map((c) => {
                  const status = connectorStatus(c);
                  return (
                    <MenuItem
                      key={c.id}
                      onClick={openConnectorSettings}
                      sx={{
                        gap: 1.5,
                        py: 0.75,
                        justifyContent: "space-between",
                      }}
                    >
                      <Box
                        sx={{ display: "flex", alignItems: "center", gap: 1 }}
                      >
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            bgcolor: status.color,
                          }}
                        />
                        <Typography sx={{ fontSize: 13 }}>{c.name}</Typography>
                      </Box>
                      <Box
                        sx={{ display: "flex", alignItems: "center", gap: 0.5 }}
                      >
                        <Typography
                          sx={{ fontSize: 12, color: "text.secondary" }}
                        >
                          {status.label}
                        </Typography>
                        {/* Shows the connector's on/off state, turned off. A
                            click on it does nothing: it must not reach the
                            row, which opens settings and would leave the
                            Chat page and the message being typed. */}
                        <Tooltip
                          title={NOT_IN_RELEASE}
                          placement="right"
                          describeChild
                        >
                          <span onClick={(e) => e.stopPropagation()}>
                            <Switch
                              size="small"
                              checked={c.is_active !== false}
                              disabled
                              inputProps={{
                                "aria-label": `Turn ${c.name} on or off`,
                                "aria-describedby": switchReasonId,
                              }}
                            />
                          </span>
                        </Tooltip>
                      </Box>
                    </MenuItem>
                  );
                })
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
                onClick={openConnectorSettings}
                sx={{ gap: 1.5, py: 1 }}
              >
                <Iconify
                  icon="solar:settings-linear"
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
                <Typography sx={{ fontSize: 13 }}>Manage connectors</Typography>
              </MenuItem>
            </Menu>
          </Box>

          {isStreaming ? (
            <IconButton
              size="small"
              onClick={onStop}
              title="Stop"
              sx={{
                bgcolor: "error.main",
                color: "error.contrastText",
                "&:hover": {
                  bgcolor: "error.dark",
                },
              }}
            >
              <Iconify icon="solar:stop-linear" width={16} />
            </IconButton>
          ) : (
            <IconButton
              size="small"
              onClick={handleSend}
              disabled={!canSend}
              title="Send"
              sx={{
                bgcolor: canSend ? "text.primary" : "transparent",
                color: canSend ? "background.paper" : "text.disabled",
                transition: "all 0.15s ease",
                "&:hover": {
                  bgcolor: canSend ? "text.secondary" : "transparent",
                },
                "&.Mui-disabled": {
                  color: "text.disabled",
                },
              }}
            >
              <Iconify icon="solar:arrow-up-linear" width={20} />
            </IconButton>
          )}
        </Box>
      </Box>

      <Typography
        variant="caption"
        sx={{
          display: "block",
          textAlign: "center",
          mt: 0.5,
          mb: 0.5,
          fontSize: 12,
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
