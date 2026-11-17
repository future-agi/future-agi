import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Collapse,
  Typography,
  useMediaQuery,
} from "@mui/material";

import { GatewayIcon } from "../../shell/gateway-icons";
import { AutoNote, AutoSection } from "./AutoSection";

// What the gateway does with model "auto", in order. Each line states what
// the gateway does: server/auto_pool.go (defaultAutoCandidates,
// inDefaultAutoPool, modelInfoFor), server/hook_policy.go checkAutoCandidate,
// routing/auto.go ChooseAuto and server/auto_route.go recordAutoDecision.
// Change them together. The ranking line promises no health handling: the
// latency and error figures auto reads are shared per provider id, and a
// candidate whose recorded calls all failed takes no error deduction.
function autoHowLines({ managedOffered = true } = {}) {
  return [
    managedOffered
      ? "Auto starts from your candidate pool. With none set, that is your providers' models, plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on; their :free and :batch variants, models priced per request, guard, safety, moderation, embedding and image models, and models released over a year before the newest are left out."
      : "Auto starts from your candidate pool. With none set, that is your providers' models.",
    `Models that cannot serve the request are set aside first: missing tools, structured output or image support, a capability the request's require header asks for, a context or output limit too small, a price above your caps, a provider whose circuit is open, or a model your key${managedOffered ? " or Future AGI model policy" : ""} does not allow.`,
    `A model on your own provider is priced from your Cost Tracking price, else your Model Database price, else the gateway's model database. A model none of them prices is set aside as having no price data: give self-hosted or renamed models a price in Org Config.${managedOffered ? " Future AGI catalogue models always use their catalog price." : ""}`,
    "The gateway scores the prompt as a simple, standard or complex task. Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
    "The next two models in the ranking become fallbacks if the first one fails.",
    "Each request log records the chosen model, the task tier and the top of the ranking. The response header x-agentcc-auto-model names the model auto chose.",
  ];
}

// A short title for each line above, in the same order.
const STEP_TITLES = [
  "Start from the candidate pool",
  "Set aside models that can't serve the request",
  "Price each model",
  "Score the task and rank",
  "Keep two fallbacks",
  "Record the choice",
];

const STEP_BUTTON_SX = {
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 1.5,
  px: 1,
  py: 1,
  borderRadius: 1,
  textAlign: "left",
  "&:hover": { bgcolor: "action.hover" },
};

const STEP_NUMBER_SX = {
  width: 20,
  height: 20,
  flexShrink: 0,
  borderRadius: "50%",
  display: "grid",
  placeItems: "center",
  bgcolor: "background.muted",
  color: "text.secondary",
  fontVariantNumeric: "tabular-nums",
};

// One step: its number and title open the gateway's full sentence below.
function Step({ number, title, text, open, onToggle }) {
  const panelId = useId();
  return (
    <Box component="li" sx={{ minWidth: 0 }}>
      <ButtonBase
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        sx={STEP_BUTTON_SX}
      >
        <Box aria-hidden sx={STEP_NUMBER_SX}>
          <Typography
            component="span"
            typography="s2"
            fontWeight="fontWeightMedium"
          >
            {number}
          </Typography>
        </Box>
        <Typography
          component="span"
          typography="s1"
          color="text.primary"
          sx={{ flex: 1, minWidth: 0 }}
        >
          {title}
        </Typography>
        <GatewayIcon
          name="chevronDown"
          size={16}
          sx={{
            color: "text.secondary",
            transform: open ? "rotate(180deg)" : "none",
            transition: (theme) =>
              theme.transitions.create("transform", {
                duration: theme.transitions.duration.shorter,
              }),
          }}
        />
      </ButtonBase>
      {/* Mounted while closed (hidden by Collapse), so the section's text
          always holds every line. */}
      <Collapse in={open} id={panelId}>
        <Box sx={{ pl: 5, pr: 1, pb: 1 }}>
          <AutoNote component="p">{text}</AutoNote>
        </Box>
      </Collapse>
    </Box>
  );
}

Step.propTypes = {
  number: PropTypes.number.isRequired,
  title: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};

// `managedOffered` false: the deployment offers no managed models, so no line
// names the managed catalog or the managed-models policy. Beside the routing
// policy (lg and up) the first step starts open; stacked, every step starts
// closed.
export default function HowAutoChooses({ managedOffered = true }) {
  const lines = autoHowLines({ managedOffered });
  const wide = useMediaQuery((theme) => theme.breakpoints.up("lg"), {
    noSsr: true,
  });
  const [open, setOpen] = useState(() => (wide ? [0] : []));
  const toggle = (index) =>
    setOpen((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
    );
  return (
    <AutoSection
      card
      title="How auto routes a request"
      note="What the gateway does, in order."
    >
      <Box
        component="ol"
        sx={{ m: 0, p: 1, listStyle: "none", display: "grid", gap: 0.5 }}
      >
        {lines.map((text, index) => (
          <Step
            key={STEP_TITLES[index]}
            number={index + 1}
            title={STEP_TITLES[index]}
            text={text}
            open={open.includes(index)}
            onToggle={() => toggle(index)}
          />
        ))}
      </Box>
    </AutoSection>
  );
}

HowAutoChooses.propTypes = { managedOffered: PropTypes.bool };
