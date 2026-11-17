import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import CellMarkdown from "src/sections/common/CellMarkdown";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useOrgMembersInfinite } from "src/api/annotation-queues/annotation-queues";
import MentionPicker from "./MentionPicker";
import { charCounter, markdownOf, mentionIdsOf } from "./commentModel";
import {
  activeMention,
  displayOfBody,
  insertMentionName,
  mentionCandidates,
  noAccessNote,
  storedOfDisplay,
} from "./mentionModel";

const SMALL_TOGGLE = {
  "& .MuiToggleButton-root": {
    py: 0,
    px: 0.75,
    typography: "s3",
    textTransform: "none",
    color: "text.secondary",
    borderColor: "divider",
    "&.Mui-selected": { color: "primary.main", bgcolor: "action.selected" },
  },
};

const selectSx = {
  minWidth: 140,
  "& .MuiInputBase-root": { typography: "s2", height: 26 },
  "& .MuiSelect-select": { py: 0.25 },
};

/**
 * W4-1 composer: Markdown with @mentions (the one org member list), an
 * anchor ("On [Whole row ▾]" and, on a result row, "On step [▾]"),
 * Write · Preview, a counter from the server's limits and ⌘↵ to post.
 * `access` = `{[user_id]: can_view}` from the server.
 */
const CommentComposer = ({
  limits,
  access,
  anchorOptions,
  stepOptions,
  onSubmit,
  onCancel,
  placeholder = "Comment, or type @ to mention someone",
  submitLabel = "Comment",
  initialBody = "",
  autoFocus = false,
  busy = false,
  focusSignal = "",
  onFocused,
  anchorKey: anchorKeyProp = "",
  testId = "comment-composer",
}) => {
  // The box shows people as `@Meera Iyer`; `picked` remembers who they
  // are, and `stored` (the token form) is what the server gets.
  const initial = useMemo(() => displayOfBody(initialBody), [initialBody]);
  const [body, setBody] = useState(initial.text);
  const [picked, setPicked] = useState(initial.mentions);
  const [caret, setCaret] = useState(initial.text.length);
  const [tab, setTab] = useState("write");
  const [anchorKey, setAnchorKey] = useState(anchorOptions?.[0]?.key ?? "row");
  // HO D1: Comment on this field puts the composer on that field (a key the
  // options offer; "Whole row" otherwise).
  useEffect(() => {
    if (
      anchorKeyProp &&
      (anchorOptions ?? []).some((a) => a.key === anchorKeyProp)
    )
      setAnchorKey(anchorKeyProp);
  }, [anchorKeyProp, anchorOptions]);
  const [stepKey, setStepKey] = useState("");
  const [active, setActive] = useState(0);
  const [closedAt, setClosedAt] = useState(null);
  const inputRef = useRef(null);
  const boxRef = useRef(null);
  const { currentOrganizationId } = useOrganization();
  // A new `focusSignal` (⌘K "Comment on this row") puts the caret here once
  // the palette has closed and handed focus back.
  useEffect(() => {
    if (!focusSignal) return undefined;
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => {
        inputRef.current?.focus();
        onFocused?.();
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [focusSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  const mention = useMemo(() => {
    const m = activeMention(body, caret);
    return m && m.start !== closedAt ? m : null;
  }, [body, caret, closedAt]);
  const { data: members = [], isLoading } = useOrgMembersInfinite(
    mention ? currentOrganizationId : null,
    mention?.query ?? "",
  );
  const canView = (id) => (access ? access[id] !== false : true);
  const candidates = useMemo(
    () => (mention ? mentionCandidates(members, mention.query, canView) : []),
    [members, mention, access], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const stored = useMemo(() => storedOfDisplay(body, picked), [body, picked]);
  const noAccess = useMemo(
    () =>
      mentionIdsOf(stored)
        .filter((id) => access && access[id] === false)
        .map((id) => picked.find((m) => m.id === id)?.name)
        .filter(Boolean),
    [stored, picked, access],
  );
  const counter = charCounter(stored, limits);
  const empty = !body.trim();
  const over = Boolean(counter?.over);

  const pick = (member) => {
    const next = insertMentionName(body, mention, member);
    setBody(next.text);
    setPicked((list) =>
      list.some((m) => m.id === member.id)
        ? list
        : [...list, { id: member.id, name: member.name }],
    );
    setCaret(next.caret);
    setActive(0);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.caret, next.caret);
      }
    });
  };

  const anchorOf = () => {
    const step = (stepOptions ?? []).find((s) => s.key === stepKey);
    if (step) return step.anchor;
    return (
      (anchorOptions ?? []).find((a) => a.key === anchorKey)?.anchor ?? {
        kind: "row",
      }
    );
  };

  const submit = async () => {
    if (empty || over || busy) return;
    const ok = await onSubmit(stored, anchorOf());
    if (ok !== false) {
      setBody("");
      setPicked([]);
      setCaret(0);
      setTab("write");
    }
  };

  const onKeyDown = (e) => {
    if (mention && candidates.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % candidates.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => (i - 1 + candidates.length) % candidates.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pick(candidates[Math.min(active, candidates.length - 1)]);
        return;
      }
    }
    if (mention && e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setClosedAt(mention.start);
      return;
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape" && onCancel) {
      e.stopPropagation();
      onCancel();
    }
    // Keep the Row view's single-key shortcuts (J / K / E …) out of typing.
    e.stopPropagation();
  };

  return (
    <Box ref={boxRef} data-testid={testId} sx={{ minWidth: 0 }}>
      {tab === "write" ? (
        <TextField
          inputRef={inputRef}
          multiline
          minRows={2}
          maxRows={8}
          fullWidth
          size="small"
          autoFocus={autoFocus}
          placeholder={placeholder}
          value={body}
          onChange={(e) => {
            setBody(e.target.value);
            setCaret(e.target.selectionStart ?? e.target.value.length);
            setActive(0);
          }}
          onSelect={(e) => setCaret(e.target.selectionStart ?? 0)}
          onKeyDown={onKeyDown}
          inputProps={{
            "aria-label": placeholder,
            "data-testid": `${testId}-input`,
          }}
          sx={{
            "& .MuiInputBase-root": {
              typography: "s1",
              bgcolor: "background.paper",
            },
          }}
        />
      ) : (
        <Box
          data-testid={`${testId}-preview`}
          sx={{
            minHeight: 56,
            p: 1,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          {empty ? (
            <Typography typography="s2" color="text.disabled">
              Nothing to preview yet.
            </Typography>
          ) : (
            <CellMarkdown text={markdownOf(stored)} fontSize={13} spacing={6} />
          )}
        </Box>
      )}
      <MentionPicker
        anchorEl={boxRef.current}
        open={Boolean(mention) && tab === "write"}
        candidates={candidates}
        active={active}
        onPick={pick}
        loading={isLoading}
      />
      {noAccess.length > 0 && (
        <Typography
          typography="s3"
          color="warning.dark"
          data-testid="mention-no-access"
          sx={{ display: "block", mt: 0.5 }}
        >
          {noAccess.map((n) => noAccessNote(n)).join(" ")}
        </Typography>
      )}
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ mt: 0.75, flexWrap: "wrap" }}
      >
        {anchorOptions?.length > 1 && (
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Typography typography="s3" color="text.secondary">
              On
            </Typography>
            <TextField
              select
              size="small"
              value={anchorKey}
              onChange={(e) => {
                setAnchorKey(e.target.value);
                setStepKey("");
              }}
              inputProps={{
                "aria-label": "Comment on",
                "data-testid": `${testId}-anchor`,
              }}
              sx={selectSx}
            >
              {anchorOptions.map((a) => (
                <MenuItem key={a.key} value={a.key} sx={{ typography: "s2" }}>
                  {a.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        )}
        {stepOptions?.length > 0 && (
          <Stack direction="row" alignItems="center" gap={0.5}>
            <Typography typography="s3" color="text.secondary">
              On step
            </Typography>
            <TextField
              select
              size="small"
              value={stepKey}
              onChange={(e) => setStepKey(e.target.value)}
              SelectProps={{ displayEmpty: true }}
              inputProps={{
                "aria-label": "On step",
                "data-testid": `${testId}-step`,
              }}
              sx={selectSx}
            >
              <MenuItem value="" sx={{ typography: "s2" }}>
                The whole answer
              </MenuItem>
              {stepOptions.map((s) => (
                <MenuItem key={s.key} value={s.key} sx={{ typography: "s2" }}>
                  {s.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        )}
        <ToggleButtonGroup
          size="small"
          exclusive
          value={tab}
          onChange={(_, v) => v && setTab(v)}
          sx={SMALL_TOGGLE}
        >
          <ToggleButton value="write">Write</ToggleButton>
          <ToggleButton value="preview">Preview</ToggleButton>
        </ToggleButtonGroup>
        <Box sx={{ flex: 1 }} />
        {counter && (
          <Typography
            typography="s3"
            data-testid={`${testId}-counter`}
            color={counter.over ? "error.main" : "text.secondary"}
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {counter.text}
          </Typography>
        )}
        {onCancel && (
          <Button size="small" onClick={onCancel} sx={{ typography: "s2" }}>
            Cancel
          </Button>
        )}
        <Button
          size="small"
          variant="contained"
          disabled={empty || over || busy}
          onClick={submit}
          data-testid={`${testId}-submit`}
          sx={{ typography: "s2" }}
        >
          {submitLabel}
          <Box
            component="span"
            sx={{ ml: 0.75, opacity: 0.7, typography: "s3" }}
          >
            ⌘↵
          </Box>
        </Button>
      </Stack>
    </Box>
  );
};

CommentComposer.propTypes = {
  limits: PropTypes.object,
  access: PropTypes.object,
  anchorOptions: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      label: PropTypes.string,
      anchor: PropTypes.object,
    }),
  ),
  stepOptions: PropTypes.array,
  onSubmit: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
  placeholder: PropTypes.string,
  submitLabel: PropTypes.string,
  initialBody: PropTypes.string,
  autoFocus: PropTypes.bool,
  busy: PropTypes.bool,
  focusSignal: PropTypes.string,
  onFocused: PropTypes.func,
  anchorKey: PropTypes.string,
  testId: PropTypes.string,
};

export default CommentComposer;
