import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { SOURCE_READ_COPY } from "./overview.constants";

const MONO = "ui-monospace, Menlo, monospace";

// Design system: a panel with a 36px header (13/500, sentence case), groups
// divided by hairlines. Tool names are identifiers, so they sit in mono 12/400;
// rules are sentences, so they read as body text.
function Group({ title, hint, items, mono }) {
  if (!items.length) return null;
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ px: 1.5, pt: 1.5, pb: 0.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1 }}>{hint}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {SOURCE_READ_COPY.count(items.length)}
        </Typography>
      </Stack>
      {items.map((item, index) => (
        <Box
          key={`${item}-${index}`}
          sx={{
            px: 1.5,
            py: 1,
            borderTop: index ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          {/* Rules are full sentences: wrap them instead of cutting them off, and
              break a long unbroken token (a URL, a tool id) rather than overflow. */}
          <Typography
            sx={{
              ...(mono
                ? { fontFamily: MONO, fontSize: 12, lineHeight: "18px" }
                : { typography: "m3" }),
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              whiteSpace: "normal",
              overflowWrap: "anywhere",
            }}
          >
            {item}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
Group.propTypes = {
  title: PropTypes.string,
  hint: PropTypes.string,
  items: PropTypes.arrayOf(PropTypes.string),
  mono: PropTypes.bool,
};

export default function SourceReadCard({ tools = [], rules = [] }) {
  const toolNames = tools.map((tool) => tool?.name).filter(Boolean);
  const ruleNames = rules.map((rule) => (typeof rule === "string" ? rule : rule?.text || rule?.name)).filter(Boolean);
  if (!toolNames.length && !ruleNames.length) return null;
  return (
    <Box sx={{ mb: 3, border: "1px solid", borderColor: "divider", borderRadius: "8px", overflow: "hidden" }}>
      <Box sx={{ px: 1.5, height: 36, display: "flex", alignItems: "center" }}>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}>
          {SOURCE_READ_COPY.title}
        </Typography>
      </Box>
      <Group title={SOURCE_READ_COPY.tools} hint={SOURCE_READ_COPY.toolsHint} items={toolNames} mono />
      <Group title={SOURCE_READ_COPY.rules} hint={SOURCE_READ_COPY.rulesHint} items={ruleNames} />
    </Box>
  );
}

SourceReadCard.propTypes = {
  tools: PropTypes.array,
  rules: PropTypes.array,
};
