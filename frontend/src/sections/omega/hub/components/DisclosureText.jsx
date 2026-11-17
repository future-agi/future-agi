import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";

import { ClosableDialog } from "src/components/custom-dialog";

import { disclosureParts, namePieces } from "../lib/disclosureNames";

// A part longer than this shows its opening words in the panel; Show all
// opens the whole text.
const PREVIEW_LENGTH = 300;

// One whole name (an environment variable, a host, a file path, a key name)
// in the code face, on one line: a line never breaks inside it. It is a box
// of its own, never wider than its column, and a name too long for a whole
// line scrolls sideways inside that box.
const NAME_SX = {
  display: "inline-block",
  maxWidth: "100%",
  overflowX: "auto",
  overflowY: "hidden",
  verticalAlign: "bottom",
  whiteSpace: "nowrap",
};

// A name with the punctuation right against it, held on one line.
const HOLD_SX = { whiteSpace: "nowrap" };

// One name of the prose and the punctuation around it. Names joined by "/"
// (OPENAI_API_KEY/ANTHROPIC_API_KEY) are a box each, and a line may break
// only at the <wbr> after a "/" between them. An opening bracket or quote
// stays on the line of the name after it, and the punctuation after a name
// (")", ";", ",", ".") on the line of the name before it, so no line starts
// with ")" or ";".
function Name({ name, before, after, typography }) {
  const pieces = namePieces(name);
  const last = pieces.length - 1;
  return pieces.map((piece, index) => (
    <React.Fragment key={index}>
      {index > 0 && <wbr />}
      <Box component="span" sx={HOLD_SX}>
        {index === 0 && before}
        <Box component="code" sx={{ ...NAME_SX, typography }}>
          {piece}
        </Box>
        {index === last && after}
      </Box>
    </React.Fragment>
  ));
}

Name.propTypes = {
  name: PropTypes.string.isRequired,
  before: PropTypes.string,
  after: PropTypes.string,
  typography: PropTypes.string.isRequired,
};

// Disclosure prose with each name in it set as code; the words around a name
// stay prose and wrap at their spaces. The text is shown exactly as given.
export function DisclosureWords({ text, codeTypography = "code_sm" }) {
  return (
    <>
      {disclosureParts(text).map((part, index) =>
        part.name ? (
          <Name
            key={index}
            name={part.name}
            before={part.before}
            after={part.after}
            typography={codeTypography}
          />
        ) : (
          <React.Fragment key={index}>{part.text}</React.Fragment>
        ),
      )}
    </>
  );
}

DisclosureWords.propTypes = {
  text: PropTypes.string,
  codeTypography: PropTypes.string,
};

// One confirmed part, complete. A long part shows its opening words, and
// Show all opens the whole text in a full-width dialog, where it reads in
// full lines rather than the side column's narrow ones.
export default function DisclosureText({ text, label }) {
  const [open, setOpen] = useState(false);
  if (text.length <= PREVIEW_LENGTH) return <DisclosureWords text={text} />;
  // The opening words end on the last whole word, without the spaces, stop,
  // comma, semicolon or colon after it, so the ellipsis follows the word.
  const opening = text
    .slice(0, PREVIEW_LENGTH)
    .replace(/\s+\S*$/, "")
    .replace(/[\s.,;:]+$/, "");
  const preview = `${opening}…`;
  return (
    <>
      <DisclosureWords text={preview} />
      {/* No side padding and no minimum width: Show all starts where the
          text above it starts. */}
      <Button
        type="button"
        size="small"
        aria-haspopup="dialog"
        aria-label={`Show all ${label.toLowerCase()} disclosure`}
        onClick={() => setOpen(true)}
        sx={{
          display: "block",
          mt: 0.5,
          px: 0,
          minWidth: 0,
          "&:focus-visible": { outline: "2px solid", outlineOffset: 2 },
        }}
      >
        Show all
      </Button>
      <ClosableDialog
        open={open}
        onClose={() => setOpen(false)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle>{`Disclosure: ${label}`}</DialogTitle>
        <DialogContent>
          <Typography
            typography="s1_2"
            color="text.primary"
            data-testid="hub-disclosure-full"
            sx={{ whiteSpace: "pre-wrap", overflowWrap: "normal" }}
          >
            <DisclosureWords text={text} codeTypography="code_md" />
          </Typography>
        </DialogContent>
      </ClosableDialog>
    </>
  );
}

DisclosureText.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};
