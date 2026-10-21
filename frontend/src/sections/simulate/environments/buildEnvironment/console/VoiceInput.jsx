import PropTypes from "prop-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { keyframes } from "@mui/system";
import { alpha } from "@mui/material/styles";
import { Box, Stack, IconButton, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const pulse = keyframes`
  0%   { transform: scaleY(0.35); }
  50%  { transform: scaleY(1); }
  100% { transform: scaleY(0.35); }
`;

// Browser-native speech-to-text. No backend: the Web Speech API runs entirely in
// the browser (Chrome/Edge/Safari; Brave disables the Google endpoint by default).
// We feed the running transcript (finalised results + the live interim guess) to
// `onTranscript`, which sets the composer draft.
const SpeechRecognition =
  typeof window !== "undefined" &&
  (window.SpeechRecognition || window.webkitSpeechRecognition);

export default function VoiceInput({ onTranscript, disabled }) {
  const [live, setLive] = useState(false);
  const [errorKind, setErrorKind] = useState(null);
  const recognitionRef = useRef(null);
  const finalRef = useRef("");
  const supported = Boolean(SpeechRecognition);

  const stop = useCallback(() => {
    const rec = recognitionRef.current;
    if (rec) {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
    setLive(false);
  }, []);

  // Tear the recogniser down on unmount so it never fires after the composer leaves.
  useEffect(() => () => stop(), [stop]);

  const start = useCallback(() => {
    if (!supported) return;
    setErrorKind(null);
    finalRef.current = "";
    const rec = new SpeechRecognition();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;

    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalRef.current += chunk;
        else interim += chunk;
      }
      onTranscript?.((finalRef.current + interim).trimStart());
    };
    rec.onerror = (event) => {
      // no-speech / aborted are benign. Split the rest so the hint is truthful:
      // a denied mic is fixable; a blocked service (e.g. Brave) is not, here.
      const code = event.error;
      if (code === "not-allowed" || code === "service-not-allowed") {
        setErrorKind("permission");
      } else if (code && !["no-speech", "aborted"].includes(code)) {
        setErrorKind("unavailable");
      }
      setLive(false);
    };
    rec.onend = () => setLive(false);

    recognitionRef.current = rec;
    try {
      rec.start();
      setLive(true);
    } catch {
      setLive(false);
    }
  }, [supported, onTranscript]);

  const title = !supported
    ? "Voice input isn't supported in this browser"
    : errorKind === "permission"
      ? "Allow microphone access to dictate"
      : errorKind === "unavailable"
        ? "Speech recognition is unavailable in this browser (try Chrome)"
        : live
          ? "Stop"
          : "Speak instead of typing";

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      {live && (
        <Stack direction="row" alignItems="center" spacing={0.375} sx={{ height: 20 }}>
          {[0, 1, 2, 3, 4].map((i) => (
            <Box
              key={i}
              sx={{
                width: 2.5, height: 16, borderRadius: 1, bgcolor: "primary.main",
                animation: `${pulse} 0.9s ease-in-out ${i * 0.11}s infinite`,
              }}
            />
          ))}
          <Typography sx={{ typography: "s3", color: "primary.main", ml: 0.75 }}>
            listening
          </Typography>
        </Stack>
      )}
      <Tooltip arrow title={title}>
        <span>
          <IconButton
            size="small"
            aria-label={live ? "Stop" : "Speak instead of typing"}
            disabled={disabled || !supported}
            onClick={live ? stop : start}
            sx={{
              color: live ? "primary.main" : "text.subtitle",
              bgcolor: (t) => (live ? alpha(t.palette.primary.main, 0.1) : "transparent"),
            }}
          >
            <Iconify icon={live ? "solar:stop-circle-bold" : "solar:microphone-3-linear"} width={18} />
          </IconButton>
        </span>
      </Tooltip>
    </Stack>
  );
}

VoiceInput.propTypes = { onTranscript: PropTypes.func, disabled: PropTypes.bool };
