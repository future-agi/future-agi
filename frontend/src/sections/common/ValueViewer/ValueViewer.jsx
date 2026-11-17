import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { formatDistanceToNowStrict } from "date-fns";
import { parse as parseYaml } from "yaml";
import CellMarkdown from "src/sections/common/CellMarkdown";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import Iconify from "src/components/iconify";
import { copyAs } from "src/sections/common/Values/copyAs";
import CodeBlock from "src/sections/common/Values/CodeBlock";
import ChatView from "src/sections/common/Values/ChatView";
import PrettyTable from "src/sections/common/Values/PrettyTable";
import ValueTree from "src/sections/common/Values/ValueTree";
import { useParams } from "react-router-dom";
import { useColumnFromPathStore } from "src/sections/common/Values/columnFromPathStore";
import ValueToolbar from "src/sections/common/Values/ValueToolbar";
import {
  AudioPlayer,
  ImageGallery,
  PdfPreview,
  VideoPlayer,
} from "src/sections/common/Values/MediaViewers";
import useValueView, {
  wantsToolbar,
} from "src/sections/common/Values/useValueView";
import { fileNameOf } from "src/sections/common/Values/sniff";
import { useValueActions } from "src/sections/common/Values/valueActions";
import { BIG_LIST } from "src/sections/common/Values/viewModes";
import { asObject, clampText, detectValueKind, rawText } from "./valueKind";
import HtmlView from "./HtmlView";

// integration review 2: one list limit with Pretty (Values/viewModes BIG_LIST).
const BIG_ARRAY = BIG_LIST;

const parseYamlText = (text) => {
  try {
    return parseYaml(text);
  } catch {
    return undefined;
  }
};

/** Structured data behind a value for the JSON / YAML / Tree modes. */
function structured(value, sniffed) {
  if (sniffed.kind === "yaml_text") return parseYamlText(value);
  // integration (RV-02): a session object read as chat (`{turns|messages, …}`)
  // shows as stored: every key and the stored roles (Chat reads the
  // normalised messages itself).
  if (sniffed.kind === "chat" && sniffed.meta?.wrapped)
    return typeof value === "string" ? asObject(value) : value;
  if (sniffed.kind === "chat") return sniffed.normalized;
  if (typeof value === "string") return asObject(value) ?? sniffed.normalized;
  return value;
}

const MismatchNote = ({ text }) => (
  <Stack direction="row" alignItems="center" gap={0.5} sx={{ mt: 0.75 }}>
    <Iconify icon="mdi:alert-outline" width={14} sx={{ color: "amber.500" }} />
    <Typography typography="s3" color="text.secondary">
      {text}
    </Typography>
  </Stack>
);

MismatchNote.propTypes = { text: PropTypes.string };

const LongText = ({ value, clampChars }) => {
  const [expanded, setExpanded] = useState(false);
  const { text, clamped, length } = clampText(value, expanded ? 0 : clampChars);
  return (
    <Box sx={{ wordBreak: "break-word" }}>
      <CellMarkdown text={text} fontSize={14} />
      {(clamped || expanded) && length > clampChars && (
        <Button
          size="small"
          variant="text"
          onClick={() => setExpanded((e) => !e)}
          sx={{ px: 0, mt: 0.5, minWidth: 0, typography: "s2" }}
        >
          {expanded
            ? "Show less"
            : `Show all (${length.toLocaleString("en-US")} chars)`}
        </Button>
      )}
    </Box>
  );
};

LongText.propTypes = { value: PropTypes.any, clampChars: PropTypes.number };

const ChatSwitch = ({ messages, normalizedFrom }) => {
  const [chatMode, setChatMode] = useState("chat");
  return (
    <Stack spacing={1}>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={chatMode}
        onChange={(_, v) => v && setChatMode(v)}
        sx={{
          alignSelf: "flex-start",
          "& .MuiToggleButton-root": { py: 0.25, px: 1, typography: "s2" },
        }}
      >
        <ToggleButton value="chat">Chat</ToggleButton>
        <ToggleButton value="tree">Tree</ToggleButton>
      </ToggleButtonGroup>
      {chatMode === "tree" ? (
        <CustomJsonViewer object={messages} defaultInspectDepth={2} />
      ) : (
        <ChatView messages={messages} normalizedFrom={normalizedFrom} />
      )}
    </Stack>
  );
};

ChatSwitch.propTypes = {
  messages: PropTypes.array,
  normalizedFrom: PropTypes.string,
};

const Embedding = ({ values }) => {
  const norm = Math.sqrt(values.reduce((s, x) => s + x * x, 0));
  return (
    <Stack gap={0.5}>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ fontFamily: "monospace" }}
      >
        {`vec[${values.length.toLocaleString("en-US")}] · norm ${norm.toFixed(3)}`}
      </Typography>
      <Typography
        typography="s2"
        sx={{ fontFamily: "monospace", wordBreak: "break-word" }}
      >
        {`[${values.slice(0, 16).join(", ")}, …]`}
      </Typography>
    </Stack>
  );
};

Embedding.propTypes = { values: PropTypes.array };

/** The Auto (sniffed best) rendering — today's `formatted`, upgraded. */
function renderAuto({
  value,
  kind,
  sniffed,
  clampChars,
  chatSwitch,
  basePath,
  transcript,
  emptyLabel,
  onAddColumn,
  onFilterBy,
  onUseAsVariable,
}) {
  switch (sniffed.kind) {
    case "image":
      return <ImageGallery urls={[sniffed.normalized]} />;
    case "images":
      return <ImageGallery urls={sniffed.normalized} />;
    case "audio":
      return <AudioPlayer url={sniffed.normalized} transcript={transcript} />;
    case "video":
      return <VideoPlayer url={sniffed.normalized} />;
    case "document":
      return /\.pdf(\?|$)|^data:application\/pdf/i.test(sniffed.normalized) ? (
        <PdfPreview url={sniffed.normalized} />
      ) : (
        <Typography
          component="a"
          href={sniffed.normalized}
          target="_blank"
          rel="noreferrer"
          typography="s1"
          color="primary.main"
          sx={{ wordBreak: "break-all" }}
        >
          {fileNameOf(sniffed.normalized)}
        </Typography>
      );
    case "chat":
      // An empty chat reads like any empty value (frozen `emptyLabel`);
      // the grid summary says `Empty chat`.
      if (!sniffed.normalized.length)
        return (
          <Typography
            typography="s1"
            color="text.disabled"
            sx={{ fontStyle: "italic" }}
          >
            {emptyLabel}
          </Typography>
        );
      return chatSwitch ? (
        <ChatSwitch
          messages={sniffed.normalized}
          normalizedFrom={sniffed.meta.normalizedFrom}
        />
      ) : (
        <ChatView
          messages={sniffed.normalized}
          normalizedFrom={sniffed.meta.normalizedFrom}
        />
      );
    case "embedding":
      return <Embedding values={sniffed.normalized} />;
    case "datetime": {
      const d = new Date(sniffed.normalized);
      return (
        <Typography typography="s1">
          {d.toLocaleString("en-US", {
            dateStyle: "medium",
            timeStyle: "short",
          })}
          <Typography component="span" typography="s2" color="text.secondary">
            {` · ${formatDistanceToNowStrict(d, { addSuffix: true })}`}
          </Typography>
        </Typography>
      );
    }
    case "yaml_text":
      return (
        <CodeBlock text={String(value)} language="yaml" bare maxHeight={0} />
      );
    default:
      break;
  }
  switch (kind) {
    case "boolean":
      return (
        <Typography typography="s1">
          {value === true || value === "true"
            ? "True"
            : value === false || value === "false"
              ? "False"
              : String(value)}
        </Typography>
      );
    case "number":
      return (
        <Typography typography="s1" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {String(value)}
        </Typography>
      );
    case "image":
      return <ImageGallery urls={[value]} />;
    case "audio":
      return <AudioPlayer url={value} transcript={transcript} />;
    case "media":
      return (
        <Typography
          component="a"
          href={value}
          target="_blank"
          rel="noreferrer"
          typography="s1"
          color="primary.main"
          sx={{ wordBreak: "break-all" }}
        >
          {value}
        </Typography>
      );
    case "json":
    case "json_string": {
      const obj = asObject(value);
      // IT-R2 G5 P5: where a nested value can become a column / filter /
      // variable (the row drawer), Auto is the tree that offers it.
      if (
        (Array.isArray(obj) && obj.length > BIG_ARRAY) ||
        (basePath && (onFilterBy || onUseAsVariable))
      )
        return (
          <ValueTree
            value={obj}
            basePath={basePath}
            onAddColumn={onAddColumn}
            onFilterBy={onFilterBy}
            onUseAsVariable={onUseAsVariable}
          />
        );
      return <CustomJsonViewer object={obj} defaultInspectDepth={2} />;
    }
    case "empty":
      return (
        <Typography
          typography="s1"
          color="text.disabled"
          sx={{ fontStyle: "italic" }}
        >
          {emptyLabel}
        </Typography>
      );
    default:
      return <LongText value={value} clampChars={clampChars} />;
  }
}

/**
 * ValueViewer — one cell value, rendered for reading (PRODUCT-SPEC §4,
 * §3.4; R2-SPEC P6 §3.2). The value is sniffed (Values/sniff.js) and shown
 * in a view mode: Auto (sniffed best) · Pretty · Chat · Markdown · HTML ·
 * Tree · JSON · YAML · Text · Raw (Form is FieldCard's, for a column with a
 * declared schema). A `View as ▾` / `Copy ▾` toolbar appears on
 * hover and focus for json / chat / media / long-text values.
 *
 * Props (frozen):
 * @param {*} props.value
 * @param {string} [props.dataType]  column data type (`text`, `json`, `image`, …)
 * @param {"formatted"|"raw"} [props.mode="formatted"]  `raw` wins over `view`
 * @param {number} [props.clampChars=1200]
 * @param {boolean} [props.chatSwitch]  Chat | Tree switch when there is no toolbar
 * @param {string} [props.emptyLabel="empty"]
 * Optional additions (R2, L6):
 * @param {string} [props.view]  controlled view mode (`auto`, `json`, …)
 * @param {boolean} [props.toolbar]  force the toolbar on / off (default: by kind)
 * @param {string} [props.fieldKey]  remembers the mode in `fa.valueView.<fieldKey>`
 * @param {string} [props.basePath]  FA path of the value (`Copy path`, Tree paths)
 * @param {boolean} [props.compact]  no toolbar, Auto only
 * @param {boolean} [props.wrap]  wrap lines in JSON / YAML / Text / Raw (default true)
 * @param {string} [props.transcript]  audio transcript (`Transcript` toggle)
 * @param {Function} [props.onViewChange]
 */
const ValueViewer = ({
  value,
  dataType,
  mode = "formatted",
  clampChars = 1200,
  chatSwitch = false,
  emptyLabel = "empty",
  view: viewProp,
  toolbar,
  fieldKey,
  basePath,
  compact = false,
  wrap: wrapProp,
  transcript,
  onViewChange,
}) => {
  const kind = useMemo(
    () => detectValueKind(value, dataType),
    [value, dataType],
  );
  const state = useValueView({
    value,
    dataType,
    fieldKey,
    basePath,
    mode,
    view: compact ? "auto" : viewProp,
    onViewChange,
  });
  const { sniffed, availability, setView, copy } = state;
  const view = compact && mode !== "raw" ? "auto" : state.view;
  // IT-R1 G5 P8: on a dataset page, a nested field can become a column.
  const { dataset: datasetId } = useParams();
  // IT-R2 G5 P5: the row drawer adds Filter by this / Use as variable.
  const actions = useValueActions();
  const onAddColumn = !basePath
    ? undefined
    : actions?.addColumn ??
      (datasetId
        ? (path) =>
            useColumnFromPathStore.getState().openFor({ datasetId, path })
        : undefined);
  const onFilterBy = basePath ? actions?.filterBy : undefined;
  const onUseAsVariable = basePath ? actions?.useAsVariable : undefined;
  const wrap = wrapProp ?? state.wrap;
  const [hover, setHover] = useState(false);
  const [full, setFull] = useState(false);

  const showToolbar =
    !compact &&
    toolbar !== false &&
    (toolbar === true || wantsToolbar(sniffed, value));

  const body = (opts = {}) =>
    renderView({
      view,
      value,
      kind,
      sniffed,
      clampChars,
      chatSwitch: chatSwitch && !showToolbar,
      basePath,
      transcript,
      emptyLabel,
      wrap,
      maxHeight: opts.maxHeight,
      onAddColumn,
      onFilterBy,
      onUseAsVariable,
    });

  if (kind === "empty" && sniffed.kind === "empty")
    return (
      <Typography
        typography="s1"
        color="text.disabled"
        sx={{ fontStyle: "italic" }}
      >
        {emptyLabel}
      </Typography>
    );

  const toolbarEl = (float, onFullScreen) => (
    <ValueToolbar
      view={view}
      availability={availability}
      onView={setView}
      wrap={wrap}
      onWrap={state.setWrap}
      onFullScreen={onFullScreen}
      onCopy={(f) => copy(f)}
      copyPath={basePath}
      float={float}
      visible={hover}
    />
  );

  if (!showToolbar)
    return (
      <>
        {body()}
        {sniffed.meta?.mismatch && (
          <MismatchNote text={sniffed.meta.mismatch} />
        )}
      </>
    );

  return (
    <Box
      sx={{ position: "relative", minHeight: 34 }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* IT-R2 G5 P6: a stable toolbar ABOVE the value (was a hover
          overlay over its first lines). */}
      <Stack
        direction="row"
        justifyContent="flex-end"
        sx={{ mb: 0.5, minHeight: 26 }}
        data-value-toolbar=""
      >
        {toolbarEl(false, () => setFull(true))}
      </Stack>
      {body()}
      {sniffed.meta?.mismatch && <MismatchNote text={sniffed.meta.mismatch} />}
      <Dialog
        open={full}
        onClose={() => setFull(false)}
        fullWidth
        maxWidth="lg"
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            typography="m3"
            fontWeight="fontWeightMedium"
            sx={{ flex: 1, fontFamily: basePath ? "monospace" : undefined }}
          >
            {basePath || fieldKey || "Value"}
          </Typography>
          {toolbarEl(false)}
          <IconButton
            size="small"
            aria-label="Close"
            onClick={() => setFull(false)}
          >
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ p: 2, maxHeight: "80vh", overflow: "auto" }}>
          {body({ maxHeight: 0 })}
        </Box>
      </Dialog>
    </Box>
  );
};

function renderView(p) {
  const {
    view,
    value,
    sniffed,
    wrap,
    maxHeight = 480,
    basePath,
    onAddColumn,
    onFilterBy,
    onUseAsVariable,
  } = p;
  switch (view) {
    case "raw":
      return (
        <CodeBlock text={rawText(value)} wrap={wrap} maxHeight={maxHeight} />
      );
    case "text": {
      const s = sniffed.normalized;
      const text =
        typeof value === "string"
          ? value
          : typeof s === "string"
            ? s
            : copyAs(value, "value");
      return <CodeBlock text={text} wrap={wrap} maxHeight={maxHeight} bare />;
    }
    case "json":
      return (
        <CodeBlock
          text={copyAs(structured(value, sniffed), "json")}
          language="json"
          wrap={wrap}
          maxHeight={maxHeight}
        />
      );
    case "yaml":
      return (
        <CodeBlock
          text={
            sniffed.kind === "yaml_text"
              ? String(value)
              : copyAs(structured(value, sniffed), "yaml")
          }
          language="yaml"
          wrap={wrap}
          maxHeight={maxHeight}
        />
      );
    case "tree":
      return (
        <ValueTree
          value={structured(value, sniffed)}
          basePath={basePath}
          maxHeight={maxHeight}
          onAddColumn={onAddColumn}
          onFilterBy={onFilterBy}
          onUseAsVariable={onUseAsVariable}
        />
      );
    case "pretty":
      return <PrettyTable value={structured(value, sniffed)} />;
    case "chat":
      return (
        <ChatView
          messages={sniffed.normalized}
          normalizedFrom={sniffed.meta?.normalizedFrom}
        />
      );
    case "markdown":
      return (
        <CellMarkdown
          text={String(sniffed.normalized ?? value)}
          fontSize={14}
        />
      );
    // W3-2 B4: HTML in a sandboxed frame (no scripts, no network).
    case "html":
      return (
        <HtmlView
          html={
            typeof value === "string" ? value : String(sniffed.normalized ?? "")
          }
          height={maxHeight ? Math.min(maxHeight, 200) : 480}
        />
      );
    default:
      return renderAuto(p);
  }
}

ValueViewer.propTypes = {
  value: PropTypes.any,
  dataType: PropTypes.string,
  mode: PropTypes.oneOf(["formatted", "raw"]),
  clampChars: PropTypes.number,
  chatSwitch: PropTypes.bool,
  emptyLabel: PropTypes.string,
  view: PropTypes.string,
  toolbar: PropTypes.bool,
  fieldKey: PropTypes.string,
  basePath: PropTypes.string,
  compact: PropTypes.bool,
  wrap: PropTypes.bool,
  transcript: PropTypes.string,
  onViewChange: PropTypes.func,
};

export default ValueViewer;
