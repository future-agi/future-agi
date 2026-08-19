import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

/**
 * The product already ships a JSON palette — `palette.syntax`, bridged to the --syntax-*
 * variables and used by the span viewer — with a per-mode dark ramp. Building a second one
 * out of success/warning/error also said the wrong thing: green strings and red booleans
 * read as pass and fail on data that carries no verdict. warning.main measured 1.28:1 on a
 * light surface, which is why numbers were all but invisible there.
 */
const TONE = {
  key: "accent.info",
  string: "syntax.string",
  number: "syntax.number",
  boolean: "syntax.boolean",
  null: "text.disabled",
};

const INDENT = "1.05rem";

const Key = ({ label }) =>
  label === null || label === undefined ? null : (
    <>
      <Box component="span" sx={{ color: TONE.key }}>
        {label}
      </Box>
      {": "}
    </>
  );

Key.propTypes = { label: PropTypes.string };

/**
 * Leaves are typed, not just printed: strings go through JSON.stringify so a value with a
 * newline or a quote in it reads as the JSON it came from rather than as layout.
 */
const leafStyle = (value) => {
  if (value === null || value === undefined) {
    return { text: "null", sx: { color: TONE.null, fontStyle: "italic" } };
  }
  if (typeof value === "string") {
    return {
      text: JSON.stringify(value),
      sx: { color: TONE.string, whiteSpace: "pre-wrap", overflowWrap: "anywhere" },
    };
  }
  if (typeof value === "number") return { text: String(value), sx: { color: TONE.number } };
  return { text: String(value), sx: { color: TONE.boolean } };
};

const Leaf = ({ label, value }) => {
  const { text, sx } = leafStyle(value);
  return (
    <Box>
      <Key label={label} />
      <Box component="span" sx={sx}>
        {text}
      </Box>
    </Box>
  );
};

Leaf.propTypes = { label: PropTypes.string, value: PropTypes.any };

/** An empty container is a fact worth showing, so it renders as a muted [] or {} leaf. */
const EmptyContainer = ({ label, isArray }) => (
  <Box>
    <Key label={label} />
    <Box component="span" sx={{ color: TONE.null, fontStyle: "italic" }}>
      {isArray ? "[]" : "{}"}
    </Box>
  </Box>
);

EmptyContainer.propTypes = { label: PropTypes.string, isArray: PropTypes.bool };

/**
 * Objects and arrays fold. Only the top two levels open themselves — deeper than that and a
 * contract of any size arrives as a wall the reader has to scroll past to find the tabs.
 */
const Node = ({ label, value, depth, forced }) => {
  if (value === null || value === undefined || typeof value !== "object") {
    return <Leaf label={label} value={value} />;
  }

  const isArray = Array.isArray(value);
  const entries = isArray
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value);

  if (entries.length === 0) return <EmptyContainer label={label} isArray={isArray} />;

  return (
    <Box
      component="details"
      open={forced === null ? depth < 2 : forced}
      sx={{
        pl: INDENT,
        "&[open] > summary .alk-mark": { transform: "rotate(90deg)" },
      }}
    >
      <Box
        component="summary"
        sx={{
          listStyle: "none",
          "&::-webkit-details-marker": { display: "none" },
          cursor: "pointer",
          ml: `-${INDENT}`,
          whiteSpace: "nowrap",
          "&:hover": { bgcolor: "action.hover", borderRadius: "3px" },
        }}
      >
        <Box
          className="alk-mark"
          component="span"
          aria-hidden
          sx={{
            display: "inline-block",
            width: INDENT,
            color: "text.secondary",
            transition: "transform 100ms",
          }}
        >
          ▸
        </Box>
        <Key label={label} />
        <Box component="span" sx={{ color: "text.secondary", fontSize: "0.9em" }}>
          {isArray ? `[${entries.length}]` : `{${entries.length}}`}
        </Box>
      </Box>
      {entries.map(([childLabel, childValue]) => (
        <Node
          key={childLabel}
          label={childLabel}
          value={childValue}
          depth={depth + 1}
          forced={forced}
        />
      ))}
    </Box>
  );
};

Node.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  depth: PropTypes.number.isRequired,
  forced: PropTypes.bool,
};

const BarButton = ({ onClick, children }) => (
  <Box
    component="button"
    type="button"
    onClick={onClick}
    sx={{
      px: 0.75,
      py: 0.2,
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "4px",
      background: (theme) => theme.palette.background.paper,
      color: "text.secondary",
      fontFamily: ALK_MONO,
      fontSize: 10.9,
      cursor: "pointer",
      "&:hover": { color: "text.primary" },
    }}
  >
    {children}
  </Box>
);

BarButton.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

const isEmpty = (value) =>
  value === null ||
  value === undefined ||
  (typeof value === "object" && Object.keys(value).length === 0);

const JsonView = ({ value }) => {
  // A <details> is uncontrolled once the reader has clicked it, so expand/collapse all works
  // by remounting the tree under a new key — the nonce is what makes a second click of the
  // same button take effect after the reader has folded something by hand.
  const [fold, setFold] = useState({ forced: null, nonce: 0 });
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setCopied(true);
    } catch {
      setCopied(false);
    }
    setTimeout(() => setCopied(false), 1400);
  };

  if (isEmpty(value)) {
    return (
      <Typography variant="body2" color="text.secondary">
        Nothing here yet.
      </Typography>
    );
  }

  return (
    <Box>
      <Box sx={{ display: "flex", gap: 0.6, mb: 0.5 }}>
        <BarButton onClick={() => setFold((was) => ({ forced: true, nonce: was.nonce + 1 }))}>
          expand all
        </BarButton>
        <BarButton onClick={() => setFold((was) => ({ forced: false, nonce: was.nonce + 1 }))}>
          collapse all
        </BarButton>
        <BarButton onClick={copy}>{copied ? "copied" : "copy json"}</BarButton>
      </Box>
      <Box
        key={fold.nonce}
        sx={{ fontFamily: ALK_MONO, fontSize: 12.5, lineHeight: 1.55, overflowX: "auto" }}
      >
        <Node label={null} value={value} depth={0} forced={fold.forced} />
      </Box>
    </Box>
  );
};

JsonView.propTypes = { value: PropTypes.any };

export default JsonView;
