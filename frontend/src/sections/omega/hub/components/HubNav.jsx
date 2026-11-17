import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import { useLocation } from "react-router-dom";

import { RouterLink } from "src/routes/components";

import { hubPaths } from "../constants";
import { useHubMe } from "../api/queries";

import HubIcon from "./HubIcon";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./tabRow";

const MAIN = [
  {
    href: hubPaths.browse,
    label: "Browse",
    match: (p) =>
      !/\/(publishers|publish|submissions|settings|review)(\/|$)/.test(p),
  },
  {
    href: hubPaths.publishers,
    label: "Publishers",
    match: (p) => p.startsWith(hubPaths.publishers),
  },
  {
    href: hubPaths.submissions,
    label: "My submissions",
    match: (p) => p.startsWith(hubPaths.submissions),
  },
  {
    href: hubPaths.settings,
    label: "Settings",
    match: (p) => p.startsWith(hubPaths.settings),
  },
];

const REVIEW = [
  {
    href: hubPaths.review,
    label: "Queue",
    match: (p) => p === hubPaths.review || /\/review\/[0-9a-f-]{36}$/.test(p),
  },
  {
    href: hubPaths.reviewReports,
    label: "Reports",
    match: (p) => p.startsWith(hubPaths.reviewReports),
  },
  {
    href: hubPaths.reviewPicks,
    label: "Picks",
    match: (p) => p.startsWith(hubPaths.reviewPicks),
  },
  {
    href: hubPaths.reviewPublishers,
    label: "Publishers",
    match: (p) => p.startsWith(hubPaths.reviewPublishers),
  },
  {
    href: hubPaths.reviewBlocklist,
    label: "Blocklist",
    match: (p) => p.startsWith(hubPaths.reviewBlocklist),
  },
  {
    href: hubPaths.reviewAudit,
    label: "Audit",
    match: (p) => p.startsWith(hubPaths.reviewAudit),
  },
];

// A tab's name with a count beside it in a quiet pill, as the entry page's
// Files and Versions tabs show theirs; no pill while the count is zero.
function TabName({ label, count }) {
  if (!count) return label;
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
    >
      {label}{" "}
      <Box
        component="span"
        sx={{
          px: 0.75,
          borderRadius: 1,
          bgcolor: "background.neutral",
          color: "text.secondary",
          typography: "s3",
          lineHeight: "18px",
        }}
      >
        {count}
      </Box>
    </Box>
  );
}

TabName.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
};

// The Hub's own tab row. "Review" appears only for maintainers (/me says so);
// the server refuses review calls from anyone else regardless. On a review
// page the main row marks Review. `counts` puts a number beside a tab's name,
// keyed by the tab's href (the review row's Queue: how many wait).
export default function HubNav({ section = "main", counts = {} }) {
  const { pathname } = useLocation();
  const { data: me } = useHubMe();
  // Publish is always offered: on a server with no Hub checker its page says
  // what is missing (UploadsUnavailable).
  const canPublish = section === "main";
  const items = section === "review" ? REVIEW : MAIN;
  const reviewing =
    section === "main" && me?.isMaintainer && /\/review(\/|$)/.test(pathname);
  const current =
    items.find((item) => item.match(pathname))?.href ||
    (reviewing ? hubPaths.review : false);
  return (
    <Box
      data-testid={`hub-nav-${section}`}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Tabs
        value={current}
        {...TAB_ROW_PROPS}
        sx={{
          flex: 1,
          minWidth: 0,
          minHeight: 40,
          "& .MuiTab-root": { minHeight: 40, typography: "s1" },
          ...TAB_ROW_SCROLL_SX,
        }}
      >
        {items.map((item) => (
          <Tab
            key={item.href}
            value={item.href}
            label={<TabName label={item.label} count={counts[item.href]} />}
            component={RouterLink}
            href={item.href}
          />
        ))}
        {section === "main" && me?.isMaintainer && (
          <Tab
            value={hubPaths.review}
            label="Review"
            component={RouterLink}
            href={hubPaths.review}
            data-testid="hub-nav-review"
          />
        )}
      </Tabs>
      {canPublish && (
        <Button
          component={RouterLink}
          href={hubPaths.publish}
          size="small"
          variant="outlined"
          startIcon={<HubIcon name="upload" size={15} />}
          sx={{ flexShrink: 0, display: { xs: "none", sm: "inline-flex" } }}
          data-testid="hub-nav-publish"
        >
          Publish
        </Button>
      )}
      {canPublish && (
        <IconButton
          component={RouterLink}
          href={hubPaths.publish}
          size="small"
          aria-label="Publish"
          sx={{
            display: { xs: "inline-flex", sm: "none" },
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <HubIcon name="upload" size={15} />
        </IconButton>
      )}
    </Box>
  );
}

HubNav.propTypes = {
  section: PropTypes.oneOf(["main", "review"]),
  counts: PropTypes.objectOf(PropTypes.number),
};
