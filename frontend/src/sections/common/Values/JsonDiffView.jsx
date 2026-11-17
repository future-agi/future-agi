import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  Popover,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import GenerateDiffText from "src/sections/common/GenerateDiffText";
import { diffLines } from "src/sections/common/ValueViewer/diffSegments";
import { diffCountsText, jsonDiff, parseIgnore } from "./jsonDiff";
import { MONO } from "./CodeBlock";

const VIEW_KEY = "fa.jsonDiff.view";
const readView = () => {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "text" ? "text" : "path";
  } catch {
    return "path";
  }
};
const writeView = (v) => {
  try {
    window.localStorage.setItem(VIEW_KEY, v);
  } catch {
    // storage unavailable
  }
};

const show = (v) => {
  if (v === undefined) return "";
  if (typeof v === "string") return JSON.stringify(v);
  try {
    const s = JSON.stringify(v);
    return s.length > 160 ? `${s.slice(0, 159)}…` : s;
  } catch {
    return String(v);
  }
};

const Change = ({ change }) => {
  const { path, op, from, to, delta } = change;
  const words =
    op === "changed" &&
    typeof from === "string" &&
    typeof to === "string" &&
    (from.length > 40 || to.length > 40);
  return (
    <Stack
      direction="row"
      gap={1.5}
      sx={{
        py: 0.625,
        borderTop: "1px solid",
        borderColor: "divider",
        minWidth: 0,
      }}
    >
      <Typography
        typography="s2"
        sx={{
          fontFamily: MONO,
          color: "text.primary",
          flexShrink: 0,
          maxWidth: "45%",
          wordBreak: "break-all",
        }}
      >
        {path}
      </Typography>
      <Box sx={{ minWidth: 0, flex: 1, typography: "s2", "& p": { m: 0 } }}>
        {op === "added" && (
          <Box
            component="span"
            sx={{
              color: "green.600",
              fontFamily: MONO,
              wordBreak: "break-word",
            }}
          >
            {`added ${show(to)}`}
          </Box>
        )}
        {op === "removed" && (
          <Box
            component="span"
            sx={{ color: "red.600", fontFamily: MONO, wordBreak: "break-word" }}
          >
            {`removed ${show(from)}`}
          </Box>
        )}
        {op === "changed" &&
          (words ? (
            diffLines(from, to, "words").map((segs, i) =>
              segs.length ? <GenerateDiffText key={i} cellText={segs} /> : null,
            )
          ) : (
            <Box
              component="span"
              sx={{ fontFamily: MONO, wordBreak: "break-word" }}
            >
              <Box component="span" sx={{ color: "red.600" }}>
                {show(from)}
              </Box>
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" → "}
              </Box>
              <Box component="span" sx={{ color: "green.600" }}>
                {show(to)}
              </Box>
              {delta !== undefined && delta !== 0 && (
                <Box
                  component="span"
                  sx={{ color: delta > 0 ? "green.600" : "red.600" }}
                >
                  {` (${delta > 0 ? "+" : ""}${delta.toLocaleString("en-US")})`}
                </Box>
              )}
            </Box>
          ))}
      </Box>
    </Stack>
  );
};

Change.propTypes = { change: PropTypes.object.isRequired };

/**
 * JsonDiff — a by-path diff of two JSON values (R2-SPEC P6 §3.6): header
 * `By path | Text` (remembered), counts `2 changed · 1 added · 0 removed`
 * and an `Ignore paths` chip (globs with `*`, key order always ignored,
 * whitespace / case toggles, arrays by index or key). `Text` falls back to
 * the line diff (`textKind`).
 *
 * Props (frozen, R2 L6):
 * @param {*} props.from
 * @param {*} props.to
 * @param {string[]} [props.ignorePaths]  initial ignore globs
 * @param {"index"|"key"} [props.arrayMatch="index"]
 * @param {(globs: string[]) => void} [props.onIgnoreChange]
 * @param {"words"|"json"} [props.textKind="json"]  the Text view's diff kind
 */
const JsonDiff = ({
  from,
  to,
  ignorePaths = [],
  arrayMatch: arrayMatchProp = "index",
  onIgnoreChange,
  textKind = "json",
}) => {
  const [view, setView] = useState(readView);
  const [globs, setGlobs] = useState(ignorePaths);
  const [draft, setDraft] = useState(ignorePaths.join(", "));
  const [anchor, setAnchor] = useState(null);
  const [ws, setWs] = useState(false);
  const [ci, setCi] = useState(false);
  const [arrayMatch, setArrayMatch] = useState(arrayMatchProp);

  const result = useMemo(
    () =>
      jsonDiff(from, to, {
        ignorePaths: globs,
        arrayMatch,
        ignoreWhitespace: ws,
        ignoreCase: ci,
      }),
    [from, to, globs, arrayMatch, ws, ci],
  );
  const textLines = useMemo(
    () => (view === "text" ? diffLines(from, to, textKind) : []),
    [view, from, to, textKind],
  );

  const applyGlobs = (text) => {
    const next = parseIgnore(text);
    setGlobs(next);
    onIgnoreChange?.(next);
  };

  return (
    <Box data-testid="json-diff">
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        flexWrap="wrap"
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 1,
          bgcolor: "inherit",
          pb: 0.75,
        }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={view}
          onChange={(_, v) => {
            if (!v) return;
            setView(v);
            writeView(v);
          }}
          sx={{
            "& .MuiToggleButton-root": {
              py: 0,
              px: 1,
              typography: "s3",
              height: 24,
            },
          }}
        >
          <ToggleButton value="path">By path</ToggleButton>
          <ToggleButton value="text">Text</ToggleButton>
        </ToggleButtonGroup>
        {view === "path" && (
          <Typography typography="s3" color="text.secondary">
            {diffCountsText(result.counts)}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {result.ignored > 0 && view === "path" && (
          <Typography typography="s3" color="text.disabled">
            {`${result.ignored} ${result.ignored === 1 ? "path" : "paths"} ignored`}
          </Typography>
        )}
        <CustomTooltip
          show
          size="small"
          placement="top"
          title="Paths to leave out of the diff"
        >
          <Box
            component="button"
            type="button"
            onClick={(e) => setAnchor(e.currentTarget)}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              px: 0.75,
              height: 22,
              border: "1px solid",
              borderColor: globs.length ? "transparent" : "divider",
              borderRadius: 0.5,
              cursor: "pointer",
              typography: "s3",
              fontFamily: globs.length ? MONO : undefined,
              bgcolor: globs.length
                ? (t) => alpha(t.palette.primary.main, 0.12)
                : "transparent",
              color: globs.length ? "primary.main" : "text.secondary",
            }}
          >
            <Iconify icon="mdi:eye-off-outline" width={14} />
            {globs.length ? `Ignore: ${globs.join(", ")}` : "Ignore paths"}
          </Box>
        </CustomTooltip>
      </Stack>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => {
          applyGlobs(draft);
          setAnchor(null);
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Stack gap={1.25} sx={{ p: 1.5, width: 300 }}>
          <TextField
            size="small"
            label="Ignore paths"
            placeholder="usage.*, *.id"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                // No keypress follow-up: it would re-click the chip that
                // regains focus when the popover closes.
                e.preventDefault();
                applyGlobs(draft);
                setAnchor(null);
              }
            }}
            helperText="Comma-separated; * matches any part"
            inputProps={{ style: { fontFamily: MONO }, spellCheck: false }}
          />
          <CustomTooltip
            show
            size="small"
            placement="left"
            title="By path never compares key order"
          >
            <FormControlLabel
              control={<Switch size="small" checked disabled />}
              label={<Typography typography="s2">Ignore key order</Typography>}
            />
          </CustomTooltip>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={ws}
                onChange={(e) => setWs(e.target.checked)}
              />
            }
            label={<Typography typography="s2">Ignore whitespace</Typography>}
          />
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={ci}
                onChange={(e) => setCi(e.target.checked)}
              />
            }
            label={<Typography typography="s2">Ignore case</Typography>}
          />
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" sx={{ flex: 1 }}>
              Match list items by
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={arrayMatch}
              onChange={(_, v) => v && setArrayMatch(v)}
              sx={{
                "& .MuiToggleButton-root": {
                  py: 0,
                  px: 1,
                  typography: "s3",
                  height: 24,
                },
              }}
            >
              <ToggleButton value="index">Index</ToggleButton>
              <ToggleButton value="key">id / name</ToggleButton>
            </ToggleButtonGroup>
          </Stack>
          <Button
            size="small"
            variant="contained"
            onClick={() => {
              applyGlobs(draft);
              setAnchor(null);
            }}
            sx={{ alignSelf: "flex-end" }}
          >
            Apply
          </Button>
        </Stack>
      </Popover>
      {view === "text" ? (
        <Box
          sx={{
            "& p": { m: 0 },
            ...(textKind === "json" && { "& p, & span": { fontFamily: MONO } }),
          }}
        >
          {textLines.map((segments, i) =>
            segments.length ? (
              <GenerateDiffText key={i} cellText={segments} />
            ) : (
              <Box key={i} sx={{ height: 18 }} />
            ),
          )}
        </Box>
      ) : result.changes.length ? (
        <Box>
          {result.changes.map((c) => (
            <Change key={`${c.op}:${c.path}`} change={c} />
          ))}
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary" sx={{ py: 0.5 }}>
          {result.ignored
            ? `No differences (${result.ignored} ${result.ignored === 1 ? "path" : "paths"} ignored)`
            : "No differences"}
        </Typography>
      )}
    </Box>
  );
};

JsonDiff.propTypes = {
  from: PropTypes.any,
  to: PropTypes.any,
  ignorePaths: PropTypes.arrayOf(PropTypes.string),
  arrayMatch: PropTypes.oneOf(["index", "key"]),
  onIgnoreChange: PropTypes.func,
  textKind: PropTypes.oneOf(["words", "json"]),
};

export default JsonDiff;
