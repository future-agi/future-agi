import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";

/**
 * Below the illustration.
 *
 * The previous body of this file was a page and a half of skeleton content —
 * file scans, typewriter sentences, tool pills, rule pills, gate rows. On top
 * of the illustration above, that was cognitive overload: two views of the
 * same information, both moving. So this now does one thing only — a small
 * live line naming the current phase — and gets out of the way.
 */

const SCRIPTS = {
  contract: [
    "Reading source files",
    "Extracting tool signatures",
    "Reading permitted argument values",
    "Finding rules in code and prose",
  ],
  environment: [
    "Deriving the world these tools act on",
    "Writing handlers for each tool",
    "Seeding rows the use cases will need",
    "Writing checks as code",
  ],
  scenarios: [
    "Reading the tool surface",
    "Drafting scenarios per use case",
    "Running the ready · solvable · not-vacuous gates",
    "Keeping only the ones that pass all three",
  ],
  evals: [
    "Reading grading needs from the contract",
    "Suggesting evals for what this environment tests",
  ],
};

const dot = keyframes`
  0%,100% { opacity: 0.4; }
  50%     { opacity: 1;   }
`;

export default function DerivingSkeleton({ tab }) {
  const script = SCRIPTS[tab] || SCRIPTS.contract;
  const [i, setI] = useState(0);
  useEffect(() => {
    setI(0);
    const t = setInterval(() => setI((n) => (n + 1) % script.length), 2000);
    return () => clearInterval(t);
  }, [tab, script.length]);

  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{ px: 2.5, py: 1.75 }}
    >
      <Box
        sx={{
          width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
          bgcolor: "text.disabled",
          animation: `${dot} 1.4s ease-in-out infinite`,
        }}
      />
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {script[i]}…
      </Typography>
    </Stack>
  );
}

DerivingSkeleton.propTypes = { tab: PropTypes.string };
