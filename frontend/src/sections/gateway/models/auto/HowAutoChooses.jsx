import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

import { Section } from "../detail/Section";

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
    `A model on your own provider is priced from your Cost Tracking price, else your Model Database price, else the gateway's model database. A model none of them prices is set aside as having no price data: give self-hosted or renamed models a price in Org Config.${managedOffered ? " Future AGI catalogue models always use the Future AGI price." : ""}`,
    "The gateway scores the prompt as a simple, standard or complex task. Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
    "The next two models in the ranking become fallbacks if the first one fails.",
    "Each request log records the chosen model, the task tier and the top of the ranking. The response header x-agentcc-auto-model names the model auto chose.",
  ];
}

// `managedOffered` false: the deployment offers no managed models, so no line
// names the managed catalog or the managed-models policy.
export default function HowAutoChooses({ managedOffered = true }) {
  const lines = autoHowLines({ managedOffered });
  return (
    <Section title="How auto chooses" note="What the gateway does, in order.">
      <Box component="ol" sx={{ m: 0, pl: 2.5, display: "grid", gap: 1 }}>
        {lines.map((text) => (
          <Typography
            key={text.slice(0, 32)}
            component="li"
            typography="s2"
            color="text.secondary"
          >
            {text}
          </Typography>
        ))}
      </Box>
    </Section>
  );
}

HowAutoChooses.propTypes = { managedOffered: PropTypes.bool };
