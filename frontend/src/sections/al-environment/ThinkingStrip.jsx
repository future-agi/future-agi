import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

/**
 * A stage can be a minute or more away while it reads a repository, and an empty screen in
 * that gap reads as broken. The label follows whatever the stream is doing and the clock
 * keeps counting, so the wait is visibly a wait rather than a hang.
 */
const ThinkingStrip = ({ label, spentUsd }) => {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const tick = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(tick);
  }, []);

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ fontFamily: ALK_MONO, fontSize: 12.2, color: "text.secondary" }}
    >
      <Box component="span" sx={{ display: "inline-flex", gap: "3px" }}>
        {[0, 0.2, 0.4].map((delay) => (
          <Box
            key={delay}
            component="i"
            sx={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              bgcolor: "text.secondary",
              animation: "alkBlink 1.2s ease-in-out infinite",
              animationDelay: `${delay}s`,
              "@keyframes alkBlink": { "0%,100%": { opacity: 0.2 }, "50%": { opacity: 1 } },
              "@media (prefers-reduced-motion: reduce)": { animation: "none", opacity: 0.6 },
            }}
          />
        ))}
      </Box>
      <Typography component="span" sx={{ fontFamily: ALK_MONO, fontSize: 12.2, color: "inherit" }}>
        {label}
      </Typography>
      <Typography
        component="span"
        sx={{ fontFamily: ALK_MONO, fontSize: 12.2, color: "inherit", fontVariantNumeric: "tabular-nums" }}
      >
        {seconds}s
      </Typography>
      {/* What the stage has spent so far. The clock alone cannot tell a long turn from a
          stalled one; a figure that keeps climbing can. */}
      {spentUsd > 0 && (
        <Typography
          component="span"
          sx={{ fontFamily: ALK_MONO, fontSize: 12.2, color: "inherit", fontVariantNumeric: "tabular-nums" }}
        >
          {`· $${spentUsd.toFixed(2)}`}
        </Typography>
      )}
    </Stack>
  );
};

ThinkingStrip.propTypes = {
  label: PropTypes.string.isRequired,
  spentUsd: PropTypes.number,
};

export default ThinkingStrip;
