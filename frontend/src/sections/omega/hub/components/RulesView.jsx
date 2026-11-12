import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";

import { formatDate } from "../lib/format";

import HubIcon from "./HubIcon";
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
          <Typography typography="s1" color="text.secondary">
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

function RuleGroup({ title, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
      <Typography
        component="h3"
        typography="s1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}

RuleGroup.propTypes = {
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
};

// The Hub rules as the reviewers enforce them, rendered from GET /rules
// (SPEC §6.8). This component holds none of the rules' text itself. They are
// folded behind one control: the page leads with the steps to publish, and
// the rules stay one click away. Folded text stays in the page (hidden), so
// find-in-page after opening and every rules-text check still see it.
export default function RulesView({ rules }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!rules) return null;
  const titleId = `${id}-title`;
  const summaryId = `${id}-summary`;
  const bodyId = `${id}-body`;
  // The date the rules took effect, as a date ("14 Oct 2026"); a value that
  // is not a date shows as the rules file gives it.
  const formatted = rules.effectiveAt ? formatDate(rules.effectiveAt) : "";
  const since = formatted === "—" ? rules.effectiveAt : formatted;
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid="hub-rules"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 1,
          px: 2,
          py: 1.75,
        }}
      >
        <Box sx={{ flex: "1 1 320px", minWidth: 0 }}>
          <Typography
            id={titleId}
            component="h2"
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            <ButtonBase
              focusRipple
              aria-expanded={open}
              aria-controls={bodyId}
              aria-describedby={summaryId}
              onClick={() => setOpen((value) => !value)}
              data-testid="hub-rules-toggle"
              sx={{
                gap: 1,
                px: 0.5,
                py: 0.25,
                mx: -0.5,
                borderRadius: 0.75,
                font: "inherit",
                color: "inherit",
                "&:hover": { color: "primary.main" },
                "&.Mui-focusVisible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: 2,
                },
              }}
            >
              <Box
                component="span"
                sx={{
                  display: "inline-flex",
                  color: "text.secondary",
                  transform: open ? "rotate(180deg)" : "none",
                  transition: "transform 150ms ease",
                  "@media (prefers-reduced-motion: reduce)": {
                    transition: "none",
                  },
                }}
              >
                <HubIcon name="chevronDown" size={16} />
              </Box>
              Hub rules
            </ButtonBase>
          </Typography>
          <Typography
            id={summaryId}
            typography="s1"
            color="text.secondary"
            sx={{ mt: 0.25 }}
          >
            What the automated checks look at, what you can publish, what a
            maintainer checks and what review means.
          </Typography>
        </Box>
        {rules.version !== undefined && rules.version !== null && (
          <Typography typography="s2" color="text.secondary" sx={{ pt: 0.5 }}>
            {`Rules version ${rules.version}${since ? `, in effect since ${since}` : ""}`}
          </Typography>
        )}
      </Box>
      <Box
        id={bodyId}
        hidden={!open}
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          px: 2,
          py: 2,
        }}
      >
        <Box
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              lg: "repeat(2, minmax(0, 1fr))",
            },
          }}
        >
          <RuleGroup title="Automated checks">
            <Items items={rules.admission || []} />
          </RuleGroup>
          <RuleGroup title="What you can publish">
            <Items items={rules.content || []} keyed />
          </RuleGroup>
          <RuleGroup title="What maintainers check">
            <Items items={rules.checklist || []} />
          </RuleGroup>
          <RuleGroup title="What review means">
            <ReviewMeans rules={rules} />
            {rules.targets?.review && (
              <Typography typography="s1" color="text.secondary">
                {rules.targets.review}
              </Typography>
            )}
          </RuleGroup>
        </Box>
      </Box>
    </Box>
  );
}

RulesView.propTypes = { rules: PropTypes.object };
