import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import { keyframes } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import FalconIcon from "src/components/falcon-icon";

import { CONSOLE_COPY } from "../build.constants";

const SHIP_SIZE = 14;
const DASH_PERIOD = 12;

const orbit = keyframes`
  to { transform: rotate(360deg); }
`;

// Transform and opacity only, so these run without layout or repaint.
const flow = keyframes`
  to { transform: translateX(${DASH_PERIOD}px); }
`;

const glide = keyframes`
  from { transform: translateX(-${SHIP_SIZE}px); }
  to { transform: translateX(100%); }
`;

const signal = keyframes`
  0% { opacity: 0.15; }
  100% { opacity: 1; }
`;

const STILL = {
  "@media (prefers-reduced-motion: reduce)": { animation: "none" },
};
// FalconIcon points down; the bar's ship flies left to right.
const shipSx = { fontSize: SHIP_SIZE, transform: "rotate(-90deg)" };

const ORBIT_SIZE = 30;

/** A small dot circling the Stop button on a faint dashed path, while work runs. */
export function StopOrbit() {
  return (
    <Box
      aria-hidden
      data-testid="builder-console-stop-orbit"
      sx={{
        position: "absolute",
        top: "50%",
        left: "50%",
        width: ORBIT_SIZE,
        height: ORBIT_SIZE,
        mt: `-${ORBIT_SIZE / 2}px`,
        ml: `-${ORBIT_SIZE / 2}px`,
        color: "text.disabled",
        pointerEvents: "none",
      }}
    >
      <svg width={ORBIT_SIZE} height={ORBIT_SIZE} style={{ display: "block" }}>
        <circle
          cx="15"
          cy="15"
          r="13.5"
          fill="none"
          stroke="currentColor"
          strokeDasharray="2 3"
        />
      </svg>
      <Box
        sx={{
          position: "absolute",
          inset: 0,
          animation: `${orbit} 3.2s linear infinite`,
          ...STILL,
        }}
      >
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: "50%",
            ml: "-2px",
            width: 4,
            height: 4,
            borderRadius: "50%",
            bgcolor: "text.primary",
          }}
        />
      </Box>
    </Box>
  );
}

function FlightPath() {
  return (
    <Box
      sx={{
        position: "relative",
        flex: 1,
        minWidth: 0,
        height: SHIP_SIZE,
        overflow: "hidden",
        color: "text.disabled",
      }}
    >
      <Box
        sx={{
          position: "absolute",
          top: 6,
          bottom: 7,
          left: -DASH_PERIOD,
          right: 0,
          background: `repeating-linear-gradient(to right, currentColor 0 5px, transparent 5px ${DASH_PERIOD}px)`,
          animation: `${flow} 1s linear infinite`,
          ...STILL,
        }}
      />
      {/* Full width, because a transform's percentage is of the element's own width. */}
      <Box
        sx={{
          position: "absolute",
          top: 0,
          width: "100%",
          display: "flex",
          color: "text.secondary",
          animation: `${glide} 7s linear infinite`,
          "@media (prefers-reduced-motion: reduce)": {
            animation: "none",
            transform: "translateX(45%)",
          },
        }}
      >
        <FalconIcon sx={shipSx} />
      </Box>
    </Box>
  );
}

function NewMessageSignal() {
  const arc = (delay) => ({
    animation: `${signal} 0.6s ease-out ${delay}s 3 alternate both`,
    ...STILL,
  });
  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        sx={{ color: "text.primary", flexShrink: 0 }}
      >
        <FalconIcon sx={shipSx} />
        <svg
          width="10"
          height="14"
          viewBox="0 0 10 14"
          fill="none"
          stroke="currentColor"
        >
          <Box component="path" d="M2 4a4 4 0 0 1 0 6" sx={arc(0)} />
          <Box component="path" d="M5 2a7 7 0 0 1 0 10" sx={arc(0.2)} />
        </svg>
      </Stack>
      <Typography
        sx={{
          typography: "s1",
          fontWeight: 500,
          color: "text.primary",
          flex: 1,
          minWidth: 0,
          textAlign: "left",
        }}
        noWrap
      >
        {CONSOLE_COPY.newMessage}
      </Typography>
    </>
  );
}

/** Docked over the list's bottom edge while the user reads further up. */
export function JumpToLatestBar({ hasNewMessage, onClick }) {
  return (
    // A zero-height anchor at the list's bottom edge, so the bar floats over it.
    <Box sx={{ position: "relative", height: 0 }}>
      <ButtonBase
        aria-label={
          hasNewMessage ? CONSOLE_COPY.jumpNewMessage : CONSOLE_COPY.jumpWorking
        }
        onClick={onClick}
        sx={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 32,
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 3,
          bgcolor: "background.paper",
          borderTop: "1px solid",
          borderColor: "divider",
          // The hover tint is see-through, so lay it over the solid bar rather
          // than replacing it, or chat text would show through.
          "&:hover": {
            backgroundImage: (theme) =>
              `linear-gradient(${theme.palette.action.hover}, ${theme.palette.action.hover})`,
          },
          "&.Mui-focusVisible": {
            outline: "2px solid",
            outlineColor: "border.focus",
            outlineOffset: -2,
          },
        }}
      >
        {hasNewMessage ? <NewMessageSignal /> : <FlightPath />}
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{ flexShrink: 0, color: "text.primary" }}
        >
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {CONSOLE_COPY.jumpToLatest}
          </Typography>
          <Iconify icon="solar:arrow-down-linear" width={14} />
        </Stack>
      </ButtonBase>
    </Box>
  );
}
JumpToLatestBar.propTypes = {
  hasNewMessage: PropTypes.bool,
  onClick: PropTypes.func,
};
