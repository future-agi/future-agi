import { Box, Button, Link, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";

const FUNNY_MESSAGES = [
  // Classic dev humor
  "Our rocket took a wrong turn at the last nebula.",
  "The AI became self-aware and decided to take a coffee break.",
  "Someone divided by zero. We told them not to.",
  "Our hamsters powering the servers need a snack break.",
  "Plot twist: the bugs were features all along... just kidding.",
  "Even rockets have bad days. This is one of them.",
  "Our code just rage-quit. We're having a talk with it.",
  "The intern pushed to prod again. Classic.",
  "Have you tried turning the universe off and on again?",
  "We blame cosmic rays. It's always cosmic rays.",

  // Space themed
  "Our navigation AI is recalculating... in another galaxy.",
  "The spacecraft's GPS said 'recalculating' and never came back.",
  "Houston had a problem. Then Houston's backup had a problem.",
  "We asked the ship's computer for help. It said 'lol no'.",
  "Somewhere in the void, a semicolon is missing.",
  "Our warp drive is powered by Stack Overflow and it's down.",
  "The autopilot is on a union-mandated break.",
  "Mission control spilled coffee on the main console. Again.",
  "Our trajectory algorithm decided to explore modern art instead.",
  "The quantum computer observed itself and collapsed.",

  // AI / ML humor
  "Our model hallucinated this error page. Wait, or did it?",
  "The neural network dreamt of electric sheep and forgot to wake up.",
  "Gradient descent went the wrong way. Way wrong.",
  "The training data included 'how to crash gracefully' apparently.",
  "Our AI assistant said 'I'll fix it later' and took a nap.",
  "The attention mechanism wasn't paying attention.",
  "We fine-tuned our model on error messages. It overfit.",
  "The transformer had more than meets the eye... including bugs.",
  "Our embeddings embedded themselves in the wrong dimension.",
  "The loss function found an unexpected minimum: this page.",

  // Programmer life
  "It works on my machine. Unfortunately, this isn't my machine.",
  "The code was perfect. Then users happened.",
  "99 bugs on the wall, take one down, patch it around... 127 bugs on the wall.",
  "We asked a developer to fix it. They added a TODO comment.",
  "The servers are fine. It's the software that's having a moment.",
  "Somewhere, a try-catch is catching feelings instead of errors.",
  "Our load balancer decided to balance its life instead.",
  "The CI/CD pipeline is more like a CI/CD suggestion.",
  "We git committed to fixing this. The merge conflict disagreed.",
  "The cache invalidated itself. Existential crisis.",

  // Absurd / random
  "A cat walked across the keyboard and deployed this.",
  "The server room is being haunted by a null pointer ghost.",
  "Our cloud floated away. We're looking for it.",
  "The database is taking a personal day.",
  "We're pretty sure the moonphase caused this.",
  "Mercury is in retrograde and so is our deployment.",
  "An alignment of planets caused a misalignment in our code.",
  "Our server's horoscope said 'avoid work today'.",
  "The bits flipped. All of them. At once.",
  "We stored the fix in blockchain. Now we can't find the wallet.",

  // Self-aware
  "This error page is the most reliable thing we've shipped today.",
  "On the bright side, this error page loaded perfectly!",
  "Look at it this way: you discovered our fanciest error page.",
  "At least the error boundary worked. Silver linings!",
  "Error pages are just surprise intermissions.",
  "This is not a bug, it's an unplanned feature demonstration.",
  "We promise the actual product is better than this page.",
  "Our error page has 100% uptime. That's... not ideal.",
  "You've found the easter egg! Just kidding, it's a bug.",
  "Congratulations! You've triggered our most artisanal error.",

  // Food metaphors
  "Our servers are overcooked. Needs more RAM seasoning.",
  "The spaghetti code finally became actual spaghetti.",
  "This is what happens when you deploy on an empty stomach.",
  "The backend is soup. Not the good kind.",
  "Our microservices became nano-services. Too micro.",
  "The stack overflowed like a bad soufflé.",
  "Our engineers are fueled by coffee. The coffee ran out.",
  "The cookies expired and they weren't even delicious.",
  "Our pipeline is more like a leaky garden hose right now.",
  "The code was half-baked. Literally. Oven timer went off mid-deploy.",

  // Office / workplace
  "The on-call engineer is 'in a meeting' (sleeping).",
  "We deployed on a Friday. We know. We KNOW.",
  "The team channel is on fire. The good kind? No, the bad kind.",
  "Someone marked the critical bug as 'won't fix'. Bold move.",
  "Our sprint retrospective predicted this. We ignored it.",
  "The standup meeting ran so long the servers gave up waiting.",
  "Our ticket board has achieved sentience and is refusing tickets.",
  "The QA team said 'told you so' and went home early.",
  "Our PM changed the requirements mid-flight. Literally mid-flight.",
  "The tech debt collectors finally came.",

  // Philosophical
  "If a server crashes and no one monitors it, does it make a log?",
  "To err is human. To err spectacularly requires a computer.",
  "In space, no one can hear your servers scream.",
  "The void stared back. The void had a 500 status code.",
  "We're not lost. We're exploring alternative uptime.",
  "This is a feature request from the chaos engineering team.",
  "Entropy always wins. Today it won a bit early.",
  "Schrodinger's server: simultaneously up and down.",
  "The error is temporary. The memes about it are forever.",
  "Life, the Universe, and Error 42.",
];

const ErrorFallbackView = ({ resetErrorBoundary }) => {
  const navigate = useNavigate();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const funnyMessage = useMemo(
    () => FUNNY_MESSAGES[Math.floor(Math.random() * FUNNY_MESSAGES.length)],
    [],
  );

  const strokeColor = isDark
    ? theme.palette.text.disabled
    : theme.palette.text.secondary;

  return (
    <Box
      display="flex"
      justifyContent="center"
      alignItems="center"
      height="100vh"
      flexDirection="column"
      overflow="hidden"
      bgcolor="background.default"
      position="relative"
    >
      {/* Background */}
      {!isDark && (
        <img
          src="/assets/errorfallback/BG.svg"
          alt=""
          style={{
            width: "100vw",
            height: "100vh",
            position: "absolute",
            objectFit: "cover",
          }}
        />
      )}

      {/* Starfield for dark mode */}
      {isDark && (
        <Box
          sx={{
            position: "absolute",
            inset: 0,
            pointerEvents: "none",
            overflow: "hidden",
            "& .star": {
              position: "absolute",
              background: "text.disabled",
              borderRadius: "50%",
            },
            "& .star-far": {
              width: "1px",
              height: "1px",
              opacity: 0.3,
              animation: "twinkle 4s ease-in-out infinite",
            },
            "& .star-mid": {
              width: 2,
              height: 2,
              opacity: 0.4,
              animation: "twinkle 3s ease-in-out infinite",
            },
            "& .star-close": {
              width: 3,
              height: 3,
              opacity: 0.6,
              animation: "twinkle 2.5s ease-in-out infinite",
            },
            "@keyframes twinkle": {
              "0%, 100%": { opacity: 0.2, transform: "scale(1)" },
              "50%": { opacity: 1, transform: "scale(1.2)" },
            },
          }}
        >
          {[
            { top: "5%", left: "8%", cls: "star star-far" },
            { top: "12%", left: "25%", cls: "star star-far" },
            { top: "8%", left: "45%", cls: "star star-far" },
            { top: "15%", left: "72%", cls: "star star-far" },
            { top: "6%", left: "88%", cls: "star star-far" },
            { top: "10%", left: "15%", cls: "star star-mid" },
            { top: "18%", left: "60%", cls: "star star-mid" },
            { top: "22%", left: "85%", cls: "star star-mid" },
            { top: "8%", left: "35%", cls: "star star-close" },
            { top: "20%", left: "78%", cls: "star star-close" },
            { top: "40%", left: "3%", cls: "star star-far" },
            { top: "55%", left: "5%", cls: "star star-far" },
            { top: "40%", right: "4%", cls: "star star-far" },
            { top: "60%", right: "6%", cls: "star star-far" },
            { top: "35%", left: "8%", cls: "star star-mid" },
            { top: "50%", right: "10%", cls: "star star-mid" },
            { top: "70%", left: "20%", cls: "star star-far" },
            { top: "75%", left: "65%", cls: "star star-far" },
            { top: "80%", left: "40%", cls: "star star-mid" },
            { top: "85%", left: "90%", cls: "star star-close" },
          ].map((s, i) => (
            <div
              key={i}
              className={s.cls}
              style={{
                top: s.top,
                left: s.left,
                right: s.right,
                animationDelay: `${i % 3 === 0 ? 0.5 : i % 3 === 1 ? 1.2 : 0.8}s`,
              }}
            />
          ))}
        </Box>
      )}

      {/* Content */}
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        flexDirection="column"
        maxWidth="600px"
        width="90%"
        zIndex={1}
        gap={3}
      >
        {/* Drifting Starship — exact match from landing page 404 */}
        <Box
          sx={{
            animation: "drift 8s ease-in-out infinite",
            "@keyframes drift": {
              "0%, 100%": { transform: "translate(0, 0) rotate(0deg)" },
              "25%": { transform: "translate(10px, -5px) rotate(2deg)" },
              "50%": { transform: "translate(5px, 5px) rotate(-1deg)" },
              "75%": { transform: "translate(-5px, -3px) rotate(1deg)" },
            },
          }}
        >
          <svg
            width="192"
            height="192"
            viewBox="0 0 200 200"
            fill="none"
            stroke={strokeColor}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {/* Drifting starship */}
            <g transform="rotate(-15, 100, 100)">
              {/* Main hull */}
              <path
                d="M100 40 L130 80 L130 140 L100 170 L70 140 L70 80 Z"
                strokeWidth="2"
              />
              {/* Cockpit */}
              <ellipse cx="100" cy="70" rx="12" ry="18" />
              <path d="M88 65 Q100 55 112 65" />
              {/* Engine pods */}
              <ellipse cx="80" cy="155" rx="6" ry="10" />
              <ellipse cx="120" cy="155" rx="6" ry="10" />
              {/* Engine glow (flickering) */}
              <path d="M80 165 L80 175" strokeWidth="3" opacity="0.4">
                <animate
                  attributeName="opacity"
                  values="0.2;0.5;0.2"
                  dur="0.5s"
                  repeatCount="indefinite"
                />
              </path>
              <path d="M120 165 L120 175" strokeWidth="3" opacity="0.4">
                <animate
                  attributeName="opacity"
                  values="0.2;0.5;0.2"
                  dur="0.5s"
                  repeatCount="indefinite"
                  begin="0.25s"
                />
              </path>
              {/* Wings */}
              <path d="M70 85 L40 70 L35 75 L35 100 L40 105 L70 95" />
              <path d="M130 85 L160 70 L165 75 L165 100 L160 105 L130 95" />
              {/* Wing tips */}
              <circle cx="35" cy="75" r="3" fill={strokeColor} />
              <circle cx="165" cy="75" r="3" fill={strokeColor} />
              {/* Hull details */}
              <line x1="85" y1="100" x2="85" y2="130" opacity="0.5" />
              <line x1="115" y1="100" x2="115" y2="130" opacity="0.5" />
              <circle cx="100" cy="115" r="8" strokeDasharray="2 2" />
            </g>

            {/* Signal waves (searching) */}
            <g>
              <circle
                cx="100"
                cy="100"
                r="60"
                strokeDasharray="4 4"
                opacity="0.2"
              >
                <animate
                  attributeName="r"
                  values="48;72;48"
                  dur="3s"
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0.3;0;0.3"
                  dur="3s"
                  repeatCount="indefinite"
                />
              </circle>
              <circle
                cx="100"
                cy="100"
                r="80"
                strokeDasharray="4 4"
                opacity="0.15"
              >
                <animate
                  attributeName="r"
                  values="64;96;64"
                  dur="3s"
                  repeatCount="indefinite"
                  begin="1s"
                />
                <animate
                  attributeName="opacity"
                  values="0.3;0;0.3"
                  dur="3s"
                  repeatCount="indefinite"
                  begin="1s"
                />
              </circle>
              <circle
                cx="100"
                cy="100"
                r="95"
                strokeDasharray="4 4"
                opacity="0.1"
              >
                <animate
                  attributeName="r"
                  values="76;114;76"
                  dur="3s"
                  repeatCount="indefinite"
                  begin="2s"
                />
                <animate
                  attributeName="opacity"
                  values="0.3;0;0.3"
                  dur="3s"
                  repeatCount="indefinite"
                  begin="2s"
                />
              </circle>
            </g>

            {/* Question mark orbit */}
            <g>
              <text
                x="165"
                y="55"
                fontSize="24"
                fill={strokeColor}
                opacity="0.4"
                fontFamily="monospace"
              >
                ?
              </text>
              <animateTransform
                attributeName="transform"
                type="rotate"
                from="0 100 100"
                to="360 100 100"
                dur="10s"
                repeatCount="indefinite"
              />
            </g>
          </svg>
        </Box>

        {/* Headline */}
        <Typography
          color="text.secondary"
          fontSize={{ xs: "24px", sm: "24px" }}
          fontWeight={600}
          lineHeight={1.3}
          fontFamily="IBM Plex Sans"
          textAlign="center"
        >
          Houston, we have a problem!
        </Typography>

        {/* Funny message */}
        <Typography
          color="text.secondary"
          fontWeight={400}
          fontFamily="IBM Plex Sans"
          textAlign="center"
          fontSize="13px"
          fontStyle="italic"
          lineHeight={1.6}
        >
          &ldquo;{funnyMessage}&rdquo;
        </Typography>

        {/* Mission Log */}
        <Box
          sx={{
            bgcolor: "background.neutral",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            p: 2,
            width: "100%",
            maxWidth: "380px",
            fontFamily: "monospace",
            fontSize: "12px",
          }}
        >
          <Typography
            fontFamily="monospace"
            fontSize="12px"
            color="text.disabled"
            mb={1}
          >
            {"// MISSION LOG"}
          </Typography>
          <Typography
            fontFamily="monospace"
            fontSize="12px"
            color="text.secondary"
            component="div"
          >
            <Box component="span" color="error.main">
              ERR:
            </Box>{" "}
            Unexpected trajectory deviation
            <br />
            <Box component="span" color="text.secondary">
              STATUS:
            </Box>{" "}
            Rocket temporarily grounded
            <br />
            <Box component="span" color="text.secondary">
              ACTION:
            </Box>{" "}
            Retry launch or return to base
          </Typography>
        </Box>

        {/* Help text */}
        <Typography
          color="text.secondary"
          fontWeight={400}
          fontFamily="IBM Plex Sans"
          textAlign="center"
          fontSize="13px"
        >
          We hit an exception while performing your action. If this keeps
          happening, our ground control team is standing by at{" "}
          <Link
            href="mailto:support@futureagi.com"
            underline="hover"
            color="primary"
            sx={{ fontWeight: 500 }}
          >
            support@futureagi.com
          </Link>
        </Typography>

        {/* Action Buttons */}
        <Box display="flex" gap={2} justifyContent="center" flexWrap="wrap">
          <Button
            onClick={resetErrorBoundary}
            variant="contained"
            color="primary"
          >
            <Iconify icon="solar:refresh-linear" width="10px" height="10px" />
            <Typography
              fontSize="12px"
              fontFamily="IBM Plex Sans"
              fontWeight={600}
              ml="8px"
            >
              Retry
            </Typography>
          </Button>
          <Button
            onClick={() => {
              navigate("/dashboard/develop");
              window.location.reload();
            }}
            variant="contained"
            color="primary"
          >
            <Iconify
              icon="solar:alt-arrow-left-linear"
              width="15px"
              height="15px"
            />
            <Typography
              fontSize="12px"
              fontFamily="IBM Plex Sans"
              fontWeight={600}
              ml="5px"
            >
              Back to home
            </Typography>
          </Button>
        </Box>
      </Box>
    </Box>
  );
};

export default ErrorFallbackView;

ErrorFallbackView.propTypes = {
  error: PropTypes.any,
  resetErrorBoundary: PropTypes.func,
};
