import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { keyframes } from "@mui/system";
import { alpha } from "@mui/material/styles";
import { Box, Stack, IconButton, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const pulse = keyframes`
  0%   { transform: scaleY(0.35); }
  50%  { transform: scaleY(1); }
  100% { transform: scaleY(0.35); }
`;

/**
 * Push to talk.
 *
 * Transcription is mocked: a fixed phrase types itself into the input while the
 * meter runs, which is enough to show the interaction without a speech service.
 * The bars are driven by index, not by audio — nothing here listens.
 */
const PHRASE =
  "add four scenarios where the caller is abusive and the agent has to stay inside policy";

export default function VoiceInput({ onTranscript, disabled }) {
  const [live, setLive] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => clearInterval(timer.current), []);

  const stop = () => {
    clearInterval(timer.current);
    timer.current = null;
    setLive(false);
  };

  const start = () => {
    setLive(true);
    let i = 0;
    timer.current = setInterval(() => {
      i += 2;
      onTranscript(PHRASE.slice(0, i));
      if (i >= PHRASE.length) stop();
    }, 26);
  };

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
      <Tooltip arrow title={live ? "Stop" : "Speak instead of typing"}>
        <span>
          <IconButton
            size="small"
            disabled={disabled}
            onClick={live ? stop : start}
            sx={{
              color: live ? "primary.main" : "text.subtitle",
              bgcolor: (t) => live ? alpha(t.palette.primary.main, 0.1) : "transparent",
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
