import { useEffect, useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";
import { isAlias, isMap, isScalar, parseDocument, visit } from "yaml";

import { SegmentedTabs } from "src/components/tabs/tabs";
import FrontmatterTable from "../components/FrontmatterTable";
import SkillMarkdownPreview from "../components/SkillMarkdownPreview";
import { LIMITS } from "../constants";
import SkillFileEditor from "./SkillFileEditor";

const SOURCE_HELP =
  "This frontmatter needs Source editing. Your original text has not been changed.";

// This is a deliberately small editing projection, not a second validator or
// serializer. Patch only the scalar's source span; never stringify the YAML map.
function documentFields(content) {
  const match = content.match(
    /^---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/,
  );
  if (
    !match ||
    new TextEncoder().encode(match[1]).length > LIMITS.MAX_FRONTMATTER_BYTES
  )
    return null;
  try {
    const doc = parseDocument(match[1], { schema: "core", uniqueKeys: true });
    if (doc.errors.length || doc.warnings.length || !isMap(doc.contents))
      return null;
    let unsupported = false;
    visit(doc, (_key, node) => {
      if (isAlias(node) || node?.anchor || node?.tag) unsupported = true;
    });
    if (unsupported) return null;
    const name = doc.get("name", true);
    const description = doc.get("description", true);
    if (
      !isScalar(name) ||
      typeof name.value !== "string" ||
      !isScalar(description) ||
      typeof description.value !== "string"
    )
      return null;
    if (
      !["PLAIN", "QUOTE_SINGLE", "QUOTE_DOUBLE"].includes(description.type) ||
      !description.range
    )
      return null;
    const [start, end] = description.range;
    if (/[\r\n]/.test(match[1].slice(start, end))) return null;
    const headerOffset = content.indexOf("\n") + 1;
    const metadata = doc.toJS({ maxAliasCount: 0 });
    delete metadata.name;
    delete metadata.description;
    return {
      name: name.value,
      description: description.value,
      descriptionStart: headerOffset + start,
      descriptionEnd: headerOffset + end,
      descriptionType: description.type,
      bodyStart: match[0].length,
      body: content.slice(match[0].length),
      eol: content.startsWith("---\r\n") ? "\r\n" : "\n",
      metadata,
    };
  } catch {
    return null;
  }
}

function replaceDescription(content, fields, value) {
  if (value === fields.description) return content;
  // A JSON string is a safe YAML double-quoted scalar, including newlines,
  // colons, quotes, and values such as "true". Retain single quotes when possible.
  const needsEscapes = [...value].some(
    (character) =>
      character.codePointAt(0) < 32 ||
      [0x85, 0x2028, 0x2029].includes(character.codePointAt(0)),
  );
  const scalar =
    fields.descriptionType === "QUOTE_SINGLE" && !needsEscapes
      ? `'${value.replace(/'/g, "''")}'`
      : JSON.stringify(value)
          .replaceAll(String.fromCharCode(0x85), "\\u0085")
          .replaceAll(String.fromCharCode(0x2028), "\\u2028")
          .replaceAll(String.fromCharCode(0x2029), "\\u2029");
  return (
    content.slice(0, fields.descriptionStart) +
    scalar +
    content.slice(fields.descriptionEnd)
  );
}

const textareaText = (text) => text.replace(/\r\n?/g, "\n");

function originalOffset(text, normalizedOffset) {
  let source = 0;
  for (let index = 0; index < normalizedOffset; index += 1) {
    source += text[source] === "\r" && text[source + 1] === "\n" ? 2 : 1;
  }
  return source;
}

function replaceBody(content, fields, value) {
  // Browsers normalize textarea newlines. Keep the untouched prefix/suffix in
  // their original bytes, including mixed line endings and the final newline.
  const before = textareaText(fields.body);
  const after = textareaText(value);
  if (before === after) return content;
  let start = 0;
  while (
    start < before.length &&
    start < after.length &&
    before[start] === after[start]
  )
    start += 1;
  let tail = 0;
  while (
    tail < before.length - start &&
    tail < after.length - start &&
    before[before.length - tail - 1] === after[after.length - tail - 1]
  )
    tail += 1;
  const eol = fields.body.match(/\r\n|\r|\n/)?.[0] || fields.eol;
  const replacement = after
    .slice(start, after.length - tail)
    .replace(/\n/g, eol);
  const delimiterNewline =
    fields.bodyStart === content.length && !content.endsWith("\n")
      ? fields.eol
      : "";
  return (
    content.slice(0, fields.bodyStart + originalOffset(fields.body, start)) +
    delimiterNewline +
    replacement +
    fields.body.slice(originalOffset(fields.body, before.length - tail))
  );
}

function DocumentEditor({
  file,
  onChange,
  onOpenPath,
  target,
  readOnly,
  sampleContent,
}) {
  const fields = useMemo(() => documentFields(file.content), [file.content]);
  const [mode, setMode] = useState(
    target?.line || !fields ? "source" : "document",
  );
  const [sourceOpened, setSourceOpened] = useState(
    Boolean(target?.line) || !fields,
  );
  const id = useId();
  useEffect(() => {
    if (target?.line) {
      setSourceOpened(true);
      setMode("source");
    }
  }, [target]);
  const change = (content) => {
    if (!readOnly && content !== file.content) onChange(content);
  };
  const selectMode = (value) => {
    if (value === "source") setSourceOpened(true);
    setMode(value);
  };
  const panel = (value) => ({
    role: "tabpanel",
    id: `${id}-${value}-panel`,
    "aria-labelledby": `${id}-${value}-tab`,
    hidden: mode !== value,
  });

  return (
    <Stack spacing={1.5} sx={{ flex: 1, minWidth: 0, minHeight: 0 }}>
      <SegmentedTabs
        value={mode}
        onChange={(_, value) => selectMode(value)}
        aria-label="Skill document view"
        sx={{ flexShrink: 0, maxWidth: "100%" }}
      >
        {["document", "source", "preview"].map((value) => (
          <Tab
            key={value}
            value={value}
            label={value[0].toUpperCase() + value.slice(1)}
            id={`${id}-${value}-tab`}
            aria-controls={`${id}-${value}-panel`}
          />
        ))}
      </SegmentedTabs>
      {!fields && <Alert severity="info">{SOURCE_HELP}</Alert>}
      {readOnly && (
        <Typography variant="s3" color="text.secondary">
          Read-only content. Editing is unavailable in this view.
        </Typography>
      )}
      <Stack
        {...panel("document")}
        spacing={2}
        sx={{
          display: mode === "document" ? "flex" : "none",
          minWidth: 0,
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          p: 0.5,
        }}
      >
        {fields ? (
          <>
            <Stack spacing={0.5}>
              <Typography variant="s3" color="text.secondary">
                Skill identity · read-only
              </Typography>
              <Typography
                variant="s1"
                sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
              >
                {fields.name}
              </Typography>
            </Stack>
            {sampleContent && (
              <Alert severity="info">
                Replace sample policies, amounts, and names with approved facts
                before publishing.
              </Alert>
            )}
            <TextField
              id={`${id}-description`}
              label="When should the agent use this skill?"
              size="small"
              fullWidth
              multiline
              minRows={2}
              maxRows={6}
              value={fields.description}
              onChange={(event) =>
                change(
                  replaceDescription(file.content, fields, event.target.value),
                )
              }
              InputProps={{ readOnly }}
              inputProps={{ maxLength: LIMITS.MAX_DESCRIPTION }}
              error={
                !fields.description.trim() ||
                fields.description.length > LIMITS.MAX_DESCRIPTION
              }
              helperText={
                !fields.description.trim()
                  ? "Describe what this skill does and when to use it."
                  : `Used for skill selection. ${fields.description.length}/${LIMITS.MAX_DESCRIPTION} characters.`
              }
            />
            <TextField
              id={`${id}-body`}
              label="Instructions"
              size="small"
              fullWidth
              multiline
              value={textareaText(fields.body)}
              onChange={(event) =>
                change(replaceBody(file.content, fields, event.target.value))
              }
              InputProps={{ readOnly, inputComponent: "textarea" }}
              inputProps={{ rows: 12 }}
              helperText="Markdown body only. Source shows the complete file; all views edit the same draft."
              sx={{
                flex: 1,
                "& .MuiInputBase-root": { flex: 1, alignItems: "stretch" },
                "& textarea": { typography: "s1" },
              }}
            />
            <Box component="details" sx={{ pb: 1 }}>
              <Typography
                component="summary"
                variant="s3"
                sx={{ cursor: "pointer", color: "text.secondary" }}
              >
                Advanced metadata · edit in Source
              </Typography>
              {Object.keys(fields.metadata).length ? (
                <FrontmatterTable frontmatter={fields.metadata} />
              ) : (
                <Typography variant="s3" color="text.secondary">
                  No additional metadata.
                </Typography>
              )}
              {Object.prototype.hasOwnProperty.call(
                fields.metadata,
                "allowed-tools",
              ) && (
                <Typography variant="s3" color="text.secondary">
                  allowed-tools may pre-approve tools in some agents; it does
                  not sandbox this skill.
                </Typography>
              )}
            </Box>
          </>
        ) : (
          <Box>
            <Button variant="outlined" onClick={() => selectMode("source")}>
              Edit original source
            </Button>
          </Box>
        )}
      </Stack>
      <Box
        {...panel("source")}
        sx={{
          display: mode === "source" ? "flex" : "none",
          flex: 1,
          minWidth: 0,
          minHeight: 0,
        }}
      >
        {sourceOpened && (
          <SkillFileEditor
            file={file}
            onChange={change}
            target={target}
            readOnly={readOnly}
          />
        )}
      </Box>
      <Box
        {...panel("preview")}
        sx={{
          display: mode === "preview" ? "block" : "none",
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          overflowY: "auto",
        }}
      >
        {mode === "preview" && (
          <SkillMarkdownPreview
            content={file.content}
            currentPath={file.path}
            onOpenPath={onOpenPath}
          />
        )}
      </Box>
    </Stack>
  );
}

const editorPropTypes = {
  file: PropTypes.shape({
    path: PropTypes.string.isRequired,
    content: PropTypes.string.isRequired,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  onOpenPath: PropTypes.func,
  target: PropTypes.shape({ line: PropTypes.number }),
  readOnly: PropTypes.bool,
  sampleContent: PropTypes.bool,
};
DocumentEditor.propTypes = editorPropTypes;

/** A controlled projection of the existing session's canonical SKILL.md string. */
export default function SkillDocumentEditor({
  file,
  onChange,
  onOpenPath,
  target,
  readOnly = false,
  sampleContent = false,
}) {
  if (file.path !== "SKILL.md")
    return (
      <SkillFileEditor
        key={file.path}
        file={file}
        onChange={(content) => {
          if (!readOnly) onChange(content);
        }}
        target={target}
        readOnly={readOnly}
      />
    );
  return (
    <DocumentEditor
      key={file.path}
      file={file}
      onChange={onChange}
      onOpenPath={onOpenPath}
      target={target}
      readOnly={readOnly}
      sampleContent={sampleContent}
    />
  );
}
SkillDocumentEditor.propTypes = editorPropTypes;
