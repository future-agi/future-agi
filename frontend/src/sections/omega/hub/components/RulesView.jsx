import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { HubSection } from "./HubStates";
import ReviewMeans from "./ReviewMeans";

function Items({ items, keyed }) {
  return (
    <Box
      component={keyed ? "ul" : "ol"}
      sx={{
        m: 0,
        pl: keyed ? 0 : 2.5,
        listStyle: keyed ? "none" : "decimal",
        display: "grid",
        gap: 0.75,
        typography: "s2",
        color: "text.tertiary",
      }}
    >
      {items.map((item) => (
        <Box component="li" key={item.key || item.step || item.text}>
          <Typography typography="s2" color="text.secondary">
            {item.title ? (
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {`${item.title}. `}
              </Box>
            ) : keyed && item.key ? (
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {`${item.key}. `}
              </Box>
            ) : null}
            {item.text}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

Items.propTypes = { items: PropTypes.array.isRequired, keyed: PropTypes.bool };

// The Hub rules as the reviewers enforce them, rendered from GET /rules
// (SPEC §6.8). This component holds none of the rules' text itself.
export default function RulesView({ rules }) {
  if (!rules) return null;
  return (
    <Box
      data-testid="hub-rules"
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" },
      }}
    >
      <HubSection title="Automated checks">
        <Items items={rules.admission || []} />
      </HubSection>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <HubSection title="What you can publish">
          <Items items={rules.content || []} keyed />
        </HubSection>
        <HubSection title="What maintainers check">
          <Items items={rules.checklist || []} />
        </HubSection>
      </Box>
      <HubSection title="What review means">
        <ReviewMeans rules={rules} />
      </HubSection>
      <HubSection title="Timing and rules">
        {rules.targets?.review && (
          <Typography typography="s2" color="text.secondary">
            {rules.targets.review}
          </Typography>
        )}
        <Typography typography="s2" color="text.secondary">
          {`Hub rules version ${rules.version}${rules.effectiveAt ? `, in effect since ${rules.effectiveAt}` : ""}.`}
        </Typography>
      </HubSection>
    </Box>
  );
}

RulesView.propTypes = { rules: PropTypes.object };
