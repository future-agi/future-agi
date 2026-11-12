import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  Component,
} from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useParticipants,
  useRoomContext,
} from "@livekit/components-react";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import logger from "src/utils/logger";
import SvgColor from "src/components/svg-color";
import {
  COVERED_DRAWER_SX,
  useDrawerCovered,
} from "src/hooks/use-drawer-stack";

const DRAWER_WIDTH = 480;

class LiveKitErrorBoundary extends Component {
  static propTypes = { children: PropTypes.node };
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {}
  render() {
    if (this.state.hasError) {
      return (
        <Box p={2}>
          <Typography typography="s2" color="error.main">
            Connection error: {String(this.state.error?.message || "Unknown")}
          </Typography>
        </Box>
      );
    }
    return this.props.children;
  }
}

function getParticipantDisplayName(name, identity, isBridgeMode = true) {
  const id = String(identity || "");
  if (id === "phone-user") return "Agent";
  if (id === "fagi-simulator" || id.includes("fagi")) return "FAGI Simulator";
  if (id.startsWith("agent-")) return isBridgeMode ? "FAGI Simulator" : "Agent";
  if (id.startsWith("listener-")) return "Listener";
  return name || identity || "Unknown";
}

// --- sessionStorage helpers ---
const STORAGE_PREFIX = "live-call-transcript-";
function loadTranscript(callId) {
  if (!callId) return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + callId);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
function saveTranscript(callId, lines) {
  if (!callId) return;
  try {
    sessionStorage.setItem(
      STORAGE_PREFIX + callId,
      JSON.stringify(lines.slice(-100)),
    );
  } catch (e) {
    logger.error("Failed to save transcript to sessionStorage", e);
  }
}
function clearTranscript(callId) {
  if (!callId) return;
  try {
    sessionStorage.removeItem(STORAGE_PREFIX + callId);
  } catch (e) {
    logger.error("Failed to clear transcript from sessionStorage", e);
  }
}

// --- Room Content ---
function RoomContent({ onDisconnected, callId }) {
  const participants = useParticipants();
  const room = useRoomContext();
  const [transcriptLines, setTranscriptLines] = useState(() =>
    loadTranscript(callId),
  );
  const transcriptEndRef = useRef(null);
  const pendingRef = useRef({ speaker: "", text: "", timer: null });

  // Persist to sessionStorage on every update
  useEffect(() => {
    saveTranscript(callId, transcriptLines);
  }, [callId, transcriptLines]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcriptLines]);

  useEffect(() => {
    if (!room) return;
    const flushPending = () => {
      const p = pendingRef.current;
      if (p.text.trim()) {
        setTranscriptLines((prev) => [
          ...prev.slice(-100),
          {
            speaker: p.speaker,
            text: p.text.trim(),
            time: new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }),
          },
        ]);
      }
      pendingRef.current = { speaker: "", text: "", timer: null };
    };
    const handleTextStream = (reader, participantIdentity) => {
      (async () => {
        try {
          const speaker =
            typeof participantIdentity === "string"
              ? participantIdentity
              : participantIdentity?.identity ||
                String(participantIdentity || "Unknown");
          const chunks = [];
          for await (const chunk of reader) {
            chunks.push(chunk);
          }
          const text = chunks.join("");
          if (!text.trim()) return;
          const p = pendingRef.current;
          if (p.speaker && p.speaker !== speaker) {
            if (p.timer) clearTimeout(p.timer);
            flushPending();
          }
          if (p.speaker === speaker) {
            if (text.length > p.text.length) p.text = text;
          } else {
            p.speaker = speaker;
            p.text = text;
          }
          if (p.timer) clearTimeout(p.timer);
          p.timer = setTimeout(flushPending, 1500);
        } catch (e) {
          logger.error("Live transcript stream error", e);
        }
      })();
    };
    room.registerTextStreamHandler("lk.transcription", handleTextStream);
    return () => {
      if (pendingRef.current.timer) clearTimeout(pendingRef.current.timer);
      if (pendingRef.current.text) {
        setTranscriptLines((prev) => [
          ...prev.slice(-100),
          {
            speaker: pendingRef.current.speaker,
            text: pendingRef.current.text.trim(),
            time: new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }),
          },
        ]);
      }
      pendingRef.current = { speaker: "", text: "", timer: null };
      try {
        room.unregisterTextStreamHandler("lk.transcription");
      } catch (e) {
        logger.error("Failed to unregister text stream handler", e);
      }
    };
  }, [room]);

  useEffect(() => {
    if (!room) return;
    const handleDisconnect = () => {
      clearTranscript(callId);
      onDisconnected?.();
    };
    room.on("disconnected", handleDisconnect);
    return () => room.off("disconnected", handleDisconnect);
  }, [room, onDisconnected, callId]);

  const remoteParticipants = participants.filter((p) => !p.isLocal);
  const isBridgeMode = remoteParticipants.some((p) => {
    const id =
      typeof p.identity === "string" ? p.identity : String(p.identity || "");
    return id === "phone-user";
  });

  const getParticipantInfo = (p) => {
    const name = typeof p.name === "string" ? p.name : "";
    const identity =
      typeof p.identity === "string" ? p.identity : String(p.identity || "");
    const displayName = getParticipantDisplayName(name, identity, isBridgeMode);
    return { name, identity, displayName, isSpeaking: p.isSpeaking };
  };

  const displayParticipants =
    remoteParticipants.length > 0
      ? remoteParticipants.map(getParticipantInfo)
      : [
          {
            identity: "agent-placeholder",
            displayName: "Agent",
            isSpeaking: false,
          },
          {
            identity: "fagi-simulator",
            displayName: "FAGI Simulator",
            isSpeaking: false,
          },
        ];

  return (
    <Box display="flex" flexDirection="column" height="100%" overflow="hidden">
      {/* Participant cards */}
      <Box
        px={2}
        pt={2}
        pb={1.5}
        borderBottom="1px solid"
        borderColor="divider"
      >
        <Stack direction="row" spacing={1.5}>
          {displayParticipants.map((p) => {
            const isAgent = p.display_name === "Agent";
            const color = isAgent ? "blue" : "orange";
            const initials = p.display_name
              .split(/[-\s]/)
              .filter(Boolean)
              .slice(0, 2)
              .map((w) => w[0]?.toUpperCase())
              .join("");
            return (
              <Box
                key={p.identity}
                sx={{
                  flex: 1,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  py: 2,
                  px: 1,
                  borderRadius: "8px",
                  bgcolor: p.isSpeaking ? `${color}.o10` : "background.neutral",
                  border: "1px solid",
                  borderColor: p.isSpeaking ? `${color}.300` : "divider",
                  transition: "all 0.2s ease",
                }}
              >
                <Box
                  sx={{
                    width: 44,
                    height: 44,
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    bgcolor: `${color}.o10`,
                    color: `${color}.500`,
                    fontSize: 13,
                    fontWeight: 600,
                    border: "1.5px solid",
                    borderColor: p.isSpeaking ? `${color}.400` : `${color}.200`,
                    mb: 1,
                    transition: "all 0.2s ease",
                  }}
                >
                  {initials}
                </Box>
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                >
                  {p.display_name}
                </Typography>
                <Typography
                  typography="s3"
                  color={p.isSpeaking ? `${color}.500` : "text.disabled"}
                  fontWeight={
                    p.isSpeaking ? "fontWeightMedium" : "fontWeightRegular"
                  }
                  sx={{ mt: 0.25 }}
                >
                  {p.isSpeaking ? "Speaking" : "Listening"}
                </Typography>
              </Box>
            );
          })}
        </Stack>
      </Box>

      {/* Transcript — chat style */}
      <Box
        flex={1}
        overflow="auto"
        px={2}
        py={1.5}
        sx={{
          "&::-webkit-scrollbar": { width: 4 },
          "&::-webkit-scrollbar-thumb": {
            bgcolor: "action.disabled",
            borderRadius: 2,
          },
        }}
      >
        {transcriptLines.length === 0 ? (
          <Box
            display="flex"
            alignItems="center"
            justifyContent="center"
            height="100%"
          >
            <Typography
              typography="s2"
              color="text.disabled"
              textAlign="center"
            >
              Waiting for conversation...
            </Typography>
          </Box>
        ) : (
          <Stack spacing={1}>
            {transcriptLines.map((line, i) => {
              const speakerName = getParticipantDisplayName(
                "",
                String(line.speaker),
                isBridgeMode,
              );
              const isAgent = speakerName === "Agent";
              return (
                <Box key={i}>
                  <Stack
                    direction="row"
                    spacing={0.5}
                    alignItems="baseline"
                    mb={0.25}
                  >
                    <Typography
                      typography="s3"
                      fontWeight={600}
                      color={isAgent ? "status.info.fg" : "status.warning.fg"}
                    >
                      {speakerName}
                    </Typography>
                    <Typography typography="s3" color="text.disabled">
                      {line.time}
                    </Typography>
                  </Stack>
                  <Typography
                    typography="s1"
                    color="text.primary"
                    sx={{ lineHeight: 1.6 }}
                  >
                    {line.text}
                  </Typography>
                </Box>
              );
            })}
            <div ref={transcriptEndRef} />
          </Stack>
        )}
      </Box>
    </Box>
  );
}

RoomContent.propTypes = {
  onDisconnected: PropTypes.func,
  callId: PropTypes.string,
};

// --- Main Component ---
export default function LiveCallMonitor({ callId, open, onClose }) {
  const [token, setToken] = useState(null);
  const [serverUrl, setServerUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [connected, setConnected] = useState(false);
  const abortControllerRef = useRef(null);
  const covered = useDrawerCovered(open);

  const fetchToken = useCallback(async () => {
    if (!callId) return;
    if (abortControllerRef.current) abortControllerRef.current.abort();
    abortControllerRef.current = new AbortController();
    setLoading(true);
    setError(null);
    try {
      const resp = await axios.get(
        endpoints.runTests.liveCallListenerToken(callId),
        { signal: abortControllerRef.current.signal },
      );
      const result = resp.data?.result || resp.data;
      setToken(result.token);
      setServerUrl(result.url);
    } catch (err) {
      if (err.name === "AbortError" || err.name === "CanceledError") return;
      setError(err.response?.data?.error || err.message || "Failed to connect");
    } finally {
      setLoading(false);
    }
  }, [callId]);

  useEffect(() => {
    if (open && callId) fetchToken();
    if (!open) {
      setToken(null);
      setServerUrl(null);
      setConnected(false);
      setError(null);
    }
    return () => {
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
  }, [open, callId, fetchToken]);

  const handleClose = () => {
    clearTranscript(callId);
    setToken(null);
    setConnected(false);
    onClose?.();
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      sx={covered ? COVERED_DRAWER_SX : undefined}
      PaperProps={{
        sx: {
          width: DRAWER_WIDTH,
          bgcolor: "background.default",
          borderRadius: "0px !important",
        },
      }}
    >
      {/* Header */}
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 1000,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
          px: 2,
          py: 1.25,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography typography="m3" fontWeight={600} color="text.primary">
            Live Call Monitor
          </Typography>
          {connected && <Chip label="LIVE" size="small" color="error" />}
        </Stack>
        <IconButton size="small" onClick={handleClose}>
          <SvgColor
            src="/assets/icons/custom/lucide--x.svg"
            sx={{ width: 14, height: 14 }}
          />
        </IconButton>
      </Box>

      {/* Body */}
      <Box flex={1} display="flex" flexDirection="column" overflow="hidden">
        {loading && (
          <Box
            display="flex"
            alignItems="center"
            justifyContent="center"
            gap={1}
            p={4}
          >
            <CircularProgress size={16} />
            <Typography typography="s2" color="text.disabled">
              Connecting...
            </Typography>
          </Box>
        )}
        {error && (
          <Box p={2}>
            <Typography typography="s2" color="status.danger.fg" mb={1}>
              {error}
            </Typography>
            <Button size="small" onClick={fetchToken}>
              Retry
            </Button>
          </Box>
        )}
        {token && serverUrl && (
          <LiveKitErrorBoundary>
            <LiveKitRoom
              serverUrl={serverUrl}
              token={token}
              connect={true}
              audio={false}
              video={false}
              onConnected={() => setConnected(true)}
              onDisconnected={() => setConnected(false)}
              onError={(error) => {
                setError(error?.message || "Connection failed");
              }}
              style={{ display: "contents" }}
            >
              <RoomAudioRenderer />
              <RoomContent onDisconnected={handleClose} callId={callId} />
            </LiveKitRoom>
          </LiveKitErrorBoundary>
        )}
      </Box>

      {/* Footer */}
      {connected && (
        <Box
          px={2}
          py={1.25}
          borderTop="1px solid"
          borderColor="divider"
          bgcolor="background.paper"
        >
          <Button
            fullWidth
            variant="outlined"
            size="small"
            onClick={handleClose}
            sx={{
              color: "text.secondary",
              borderColor: "divider",
              "&:hover": {
                borderColor: "status.danger.border",
                color: "status.danger.fg",
                bgcolor: "status.danger.bg",
              },
            }}
          >
            Stop Listening
          </Button>
        </Box>
      )}
    </Drawer>
  );
}

LiveCallMonitor.propTypes = {
  callId: PropTypes.string,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
