import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemAvatar,
  ListItemButton,
  ListItemText,
  Paper,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import { fDateTime, fToNowStrict } from "src/utils/format-time";
import {
  useCreateDiscussionComment,
  useReopenDiscussionThread,
  useResolveDiscussionThread,
  useToggleDiscussionReaction,
} from "src/api/annotation-queues/annotation-queues";

const MAX_MENTION_SUGGESTIONS = 6;
const QUICK_REACTION_OPTIONS = ["👍", "✅", "👀", "❤️", "🎉", "🚀"];
const EMOJI_REACTION_GROUPS = [
  {
    title: "Common",
    options: [
      ["👍", "Thumbs up"],
      ["👎", "Thumbs down"],
      ["✅", "Done"],
      ["👀", "Looking"],
      ["❤️", "Heart"],
      ["🎉", "Celebrate"],
      ["🚀", "Ship it"],
      ["💯", "Perfect"],
      ["🙏", "Thanks"],
      ["👏", "Great work"],
      ["🔥", "Important"],
      ["⭐", "Star"],
    ],
  },
  {
    title: "Review",
    options: [
      ["🟢", "Looks good"],
      ["🟡", "Needs attention"],
      ["🔴", "Blocked"],
      ["⚠️", "Warning"],
      ["❓", "Question"],
      ["💡", "Idea"],
      ["📝", "Note"],
      ["🔍", "Investigate"],
      ["🧪", "Test"],
      ["🛠️", "Fix"],
      ["📌", "Pin"],
      ["⏳", "Waiting"],
    ],
  },
  {
    title: "Tone",
    options: [
      ["😀", "Happy"],
      ["😄", "Nice"],
      ["🙂", "Okay"],
      ["🤔", "Thinking"],
      ["😕", "Unsure"],
      ["😬", "Concern"],
      ["😮", "Surprised"],
      ["😭", "Issue"],
      ["🙌", "Win"],
      ["🤝", "Agree"],
      ["🙋", "Follow up"],
      ["💪", "Strong"],
    ],
  },
  {
    title: "Objects",
    options: [
      ["📈", "Improving"],
      ["📉", "Regression"],
      ["📊", "Data"],
      ["📎", "Reference"],
      ["🔗", "Link"],
      ["🔒", "Private"],
      ["🔓", "Open"],
      ["🧠", "Reasoning"],
      ["🎯", "Target"],
      ["🏁", "Finish"],
      ["✨", "Polish"],
      ["🧭", "Direction"],
    ],
  },
];
const EMOJI_REACTION_OPTIONS = EMOJI_REACTION_GROUPS.flatMap((group) =>
  group.options.map(([emoji, label]) => ({ emoji, label, group: group.title })),
);
const THREAD_STATUS_META = {
  open: {
    label: "Open",
    color: "info",
    icon: "solar:chat-round-dots-bold",
  },
  reopened: {
    label: "Reopened",
    color: "warning",
    icon: "solar:restart-bold",
  },
  addressed: {
    label: "Addressed",
    color: "info",
    icon: "solar:check-read-bold",
  },
  resolved: {
    label: "Resolved",
    color: "success",
    icon: "solar:check-circle-bold",
  },
};

function neutralBorder(theme, opacity = 0.12) {
  return alpha(theme.palette.text.primary, opacity);
}

function quietSurface(theme, opacity = 0.025) {
  return alpha(
    theme.palette.text.primary,
    theme.palette.mode === "dark" ? opacity * 2 : opacity,
  );
}

function statusTone(theme, color = "info") {
  const palette = theme.palette[color] || theme.palette.info;
  const isDark = theme.palette.mode === "dark";
  return {
    main: palette.main,
    text: isDark ? palette.light : palette.dark,
    bg: alpha(palette.main, isDark ? 0.16 : 0.08),
    bgHover: alpha(palette.main, isDark ? 0.22 : 0.12),
    border: alpha(palette.main, isDark ? 0.38 : 0.26),
  };
}

function statusChipSx(color) {
  return (theme) => {
    const tone = statusTone(theme, color);
    return {
      height: 22,
      borderRadius: 0.75,
      bgcolor: quietSurface(theme, 0.035),
      border: `1px solid ${neutralBorder(theme, theme.palette.mode === "dark" ? 0.16 : 0.1)}`,
      color: "text.secondary",
      fontWeight: 650,
      "& .MuiChip-icon": { color: "inherit" },
      "& svg": { color: tone.main },
    };
  };
}

function neutralChipSx(theme) {
  return {
    height: 22,
    borderRadius: 0.75,
    bgcolor: quietSurface(theme, 0.04),
    border: `1px solid ${neutralBorder(theme, theme.palette.mode === "dark" ? 0.16 : 0.1)}`,
    color: "text.primary",
    fontWeight: 650,
    "& .MuiChip-icon": { color: "inherit" },
  };
}

function actionButtonSx(color) {
  return (theme) => {
    const tone = statusTone(theme, color);
    return {
      borderRadius: 0.75,
      borderColor: tone.border,
      bgcolor: tone.bg,
      color: tone.text,
      fontWeight: 700,
      "&:hover": {
        borderColor: tone.border,
        bgcolor: tone.bgHover,
      },
      "&.Mui-disabled": {
        borderColor: alpha(theme.palette.action.disabled, 0.2),
      },
    };
  };
}

function isReactionEmoji(value) {
  const emoji = String(value || "").trim();
  if (!emoji || emoji.length > 16 || /\s/u.test(emoji)) return false;
  try {
    return (
      /[\p{Extended_Pictographic}\p{Emoji_Presentation}\p{Regional_Indicator}]/u.test(
        emoji,
      ) && !/[\p{L}]/u.test(emoji)
    );
  } catch {
    return emoji.length <= 4 && !/[A-Za-z0-9\s]/.test(emoji);
  }
}

function reactionCount(reaction) {
  const count = Number(reaction?.count || 0);
  return Number.isFinite(count) ? count : 0;
}

function timelineTimeMeta(value) {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;

  let exact = String(value);
  let relative = exact;
  try {
    exact = fDateTime(date) || exact;
  } catch {
    exact = String(value);
  }
  try {
    relative = fToNowStrict(date) || exact;
  } catch {
    relative = exact;
  }
  return { relative, exact };
}

function commentActionLabel(action) {
  return (
    {
      resolve: "Resolved",
      reopen: "Reopened",
      approve: "Approved",
      request_changes: "Requested changes",
    }[action] || String(action || "").replaceAll("_", " ")
  );
}

function threadTimelineParts(thread) {
  const parts = [];
  const firstCommentAt = thread?.comments?.[0]?.created_at;
  const created = timelineTimeMeta(thread?.created_at || firstCommentAt);
  const addressed = timelineTimeMeta(thread?.addressed_at);
  const resolved = timelineTimeMeta(thread?.resolved_at);
  const reopened = timelineTimeMeta(thread?.reopened_at);
  if (created) {
    parts.push({
      key: "created",
      label: `Created ${created.relative}`,
      exact: created.exact,
    });
  }
  if (addressed) {
    parts.push({
      key: "addressed",
      label: `Addressed ${addressed.relative}`,
      exact: addressed.exact,
    });
  }
  if (resolved) {
    parts.push({
      key: "resolved",
      label: `Resolved ${resolved.relative}`,
      exact: resolved.exact,
    });
  }
  if (reopened) {
    parts.push({
      key: "reopened",
      label: `Reopened ${reopened.relative}`,
      exact: reopened.exact,
    });
  }
  return parts;
}

function threadStatusMeta(thread) {
  return THREAD_STATUS_META[threadStatus(thread)] || THREAD_STATUS_META.open;
}

function memberId(member) {
  return member?.user_id || member?.id || "";
}

function memberLabel(member) {
  return member?.name || member?.email || "Unknown";
}

function memberInitial(member) {
  return memberLabel(member).slice(0, 1).toUpperCase() || "?";
}

function memberSearchText(member) {
  return [member?.name, member?.email, member?.email?.split("@")[0]]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function mentionAliases(member) {
  return [
    member?.name ? `@${member.name}` : null,
    member?.email ? `@${member.email}` : null,
    member?.email ? `@${member.email.split("@")[0]}` : null,
  ].filter(Boolean);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function draftContainsMentionAlias(draft, alias) {
  const pattern = new RegExp(
    `(^|[^\\w@.-])${escapeRegExp(alias)}(?=$|[\\s,;:!?\\)\\]]|\\.(?=$|\\s))`,
    "i",
  );
  return pattern.test(draft);
}

function normalizeMentionMarkdown(text) {
  return String(text || "").replace(
    /@\[([^[\]]{1,100})\]\(user:[^)]+\)/g,
    "@$1",
  );
}

function commentAuthor(comment) {
  return comment?.reviewer_name || comment?.reviewer_email || "Unknown";
}

function commentScope(comment) {
  const scopeParts = [comment?.label_name || "Item"];
  if (comment?.target_annotator_name || comment?.target_annotator_email) {
    scopeParts.push(
      `for ${comment.target_annotator_name || comment.target_annotator_email}`,
    );
  }
  return scopeParts.join(" / ");
}

function commentSearchText(comment) {
  return [
    normalizeMentionMarkdown(comment?.comment),
    comment?.reviewer_name,
    comment?.reviewer_email,
    comment?.label_name,
    comment?.target_annotator_name,
    comment?.target_annotator_email,
    ...(comment?.mentioned_users || []).flatMap((user) => [
      user?.name,
      user?.email,
    ]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function isDiscussionComment(comment) {
  return comment?.action === "comment";
}

function findReferenceTrigger(value, caretIndex) {
  const prefix = value.slice(0, caretIndex);
  const match = prefix.match(/(^|\s)([@#])([^\s@#]*)$/);
  if (!match) return null;

  const marker = match[2];
  const query = match[3] || "";
  return {
    type: marker === "@" ? "person" : "scope",
    query,
    start: prefix.length - query.length - 1,
    end: caretIndex,
  };
}

function findMentionTrigger(value, caretIndex) {
  const trigger = findReferenceTrigger(value, caretIndex);
  return trigger?.type === "person" ? trigger : null;
}

function mentionedIdsFromDraft(draft, members) {
  const ids = new Set();
  const lowerDraft = draft.toLowerCase();

  for (const member of members || []) {
    const id = memberId(member);
    if (!id) continue;
    const hasInlineMention = mentionAliases(member).some((alias) =>
      draftContainsMentionAlias(lowerDraft, alias.toLowerCase()),
    );
    if (hasInlineMention) ids.add(String(id));
  }

  return Array.from(ids);
}

function uniqueMembers(members) {
  const seen = new Set();
  return (members || []).filter((member) => {
    const id = memberId(member);
    if (!id || seen.has(String(id))) return false;
    seen.add(String(id));
    return true;
  });
}

function labelId(label) {
  return String(label?.label_id || label?.id || "");
}

function safeTag(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function labelTag(label) {
  return safeTag(label?.name) || safeTag(labelId(label)) || "label";
}

function scopeSearchText(option) {
  return [option?.token, option?.label, option?.secondary]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function insertAtTrigger(draft, trigger, token) {
  if (!trigger)
    return `${draft}${draft && !draft.endsWith(" ") ? " " : ""}${token} `;
  return `${draft.slice(0, trigger.start)}${token} ${draft.slice(trigger.end)}`;
}

function labelIdFromDraft(draft, labels) {
  const tokens = new Set(
    String(draft || "")
      .toLowerCase()
      .match(/#[a-z0-9_-]+/g) || [],
  );
  for (const label of labels || []) {
    if (tokens.has(`#${labelTag(label)}`)) return labelId(label);
  }
  return "";
}

function threadStatus(thread) {
  return thread?.status || thread?.thread_status || "open";
}

function threadIsResolved(thread) {
  return threadStatus(thread) === "resolved";
}

function threadScopeLabel(thread, itemLabel = "Item") {
  const parts = [
    thread?.label_name ? `#${safeTag(thread.label_name)}` : itemLabel,
  ];
  if (thread?.target_annotator_name || thread?.target_annotator_email) {
    parts.push(
      `@${thread.target_annotator_name || thread.target_annotator_email}`,
    );
  }
  return parts.join(" ");
}

function normalizeThreads({ threads = [], comments = [], itemLabel }) {
  const byId = new Map();
  for (const thread of threads || []) {
    const id = String(thread?.id || "");
    if (!id) continue;
    byId.set(id, {
      ...thread,
      comments: Array.isArray(thread.comments) ? thread.comments : [],
    });
  }

  for (const comment of (comments || []).filter(isDiscussionComment)) {
    const threadId = String(comment?.thread_id || "");
    if (threadId && byId.has(threadId)) {
      const existing = byId.get(threadId);
      const existingComments = Array.isArray(existing.comments)
        ? existing.comments
        : [];
      const commentId = String(comment?.id || "");
      const alreadyIncluded =
        commentId &&
        existingComments.some((item) => String(item?.id || "") === commentId);
      if (!alreadyIncluded) {
        existing.comments = [...existingComments, comment];
      }
      continue;
    }
    const id =
      threadId || `comment:${comment?.id || comment?.created_at || byId.size}`;
    if (!id) continue;
    byId.set(id, {
      id,
      status: comment?.thread_status || "open",
      scope: comment?.thread_scope || "item",
      blocking: Boolean(comment?.blocking),
      label_id: comment?.label_id || null,
      label_name: comment?.label_name || null,
      target_annotator_id: comment?.target_annotator_id || null,
      target_annotator_name: comment?.target_annotator_name || null,
      target_annotator_email: comment?.target_annotator_email || null,
      created_by_name: comment?.reviewer_name,
      created_by_email: comment?.reviewer_email,
      created_at: comment?.created_at || null,
      addressed_at: comment?.addressed_at || null,
      resolved_at: comment?.resolved_at || null,
      reopened_at: comment?.reopened_at || null,
      comments: [comment],
    });
  }

  return Array.from(byId.values())
    .map((thread) => ({
      ...thread,
      scopeLabel: threadScopeLabel(thread, itemLabel),
      comments: [...(thread.comments || [])].sort((a, b) =>
        String(a?.created_at || "").localeCompare(String(b?.created_at || "")),
      ),
    }))
    .sort((a, b) =>
      String(b?.created_at || "").localeCompare(String(a?.created_at || "")),
    );
}

function scopeTargetForTag(token, labels, comment) {
  const tag = safeTag(String(token || "").replace(/^#/, ""));
  if (!tag) return null;
  if (tag === "item" || tag === "this") return { labelId: "" };

  const label = (labels || []).find((candidate) => {
    const id = labelId(candidate);
    return labelTag(candidate) === tag || safeTag(id) === tag;
  });
  if (label) return { labelId: labelId(label) };

  if (comment?.label_id && safeTag(comment?.label_name) === tag) {
    return { labelId: comment.label_id };
  }

  return null;
}

function commentDisplayParts({ text, aliases, labels, comment }) {
  const matches = [];
  const lowerText = text.toLowerCase();

  for (const alias of aliases) {
    let cursor = 0;
    const lowerAlias = alias.toLowerCase();
    while (cursor < lowerText.length) {
      const index = lowerText.indexOf(lowerAlias, cursor);
      if (index === -1) break;
      matches.push({
        index,
        end: index + alias.length,
        type: "mention",
        text: text.slice(index, index + alias.length),
      });
      cursor = index + Math.max(alias.length, 1);
    }
  }

  const tagRegex = /#[a-z0-9_-]+/gi;
  for (const match of text.matchAll(tagRegex)) {
    const target = scopeTargetForTag(match[0], labels, comment);
    if (!target) continue;
    matches.push({
      index: match.index,
      end: match.index + match[0].length,
      type: "scope",
      text: match[0],
      target,
    });
  }

  const ordered = matches
    .sort((a, b) => a.index - b.index || b.end - a.end)
    .filter((match, index, all) => {
      const previous = all
        .slice(0, index)
        .find((candidate) => candidate.end > match.index);
      return !previous;
    });

  const parts = [];
  let cursor = 0;
  for (const match of ordered) {
    if (match.index > cursor) {
      parts.push({ text: text.slice(cursor, match.index), type: "text" });
    }
    parts.push(match);
    cursor = match.end;
  }
  if (cursor < text.length) {
    parts.push({ text: text.slice(cursor), type: "text" });
  }
  return parts.length ? parts : [{ text, type: "text" }];
}

function HighlightedComment({ comment, labels = [], onFocusScope }) {
  const text = normalizeMentionMarkdown(comment?.comment);
  const aliases = (comment?.mentioned_users || [])
    .flatMap(mentionAliases)
    .sort((a, b) => b.length - a.length);
  const parts = commentDisplayParts({ text, aliases, labels, comment });

  return (
    <Typography
      component="div"
      variant="body2"
      sx={{ mt: 0.5, whiteSpace: "pre-wrap" }}
    >
      {parts.map((part, index) =>
        part.type === "mention" ? (
          <Box
            key={`${part.text}-${index}`}
            component="span"
            sx={{
              px: 0.35,
              py: 0.1,
              borderRadius: 0.5,
              color: (theme) => statusTone(theme, "info").text,
              bgcolor: (theme) => statusTone(theme, "info").bg,
              fontWeight: 700,
            }}
          >
            {part.text}
          </Box>
        ) : part.type === "scope" && onFocusScope ? (
          <Box
            key={`${part.text}-${index}`}
            component="button"
            type="button"
            aria-label={`Focus ${part.text}`}
            onClick={() =>
              onFocusScope?.({
                labelId: part.target?.labelId || "",
                targetAnnotatorId: "",
              })
            }
            sx={{
              display: "inline",
              px: 0.35,
              py: 0.1,
              border: 0,
              borderRadius: 0.5,
              color: (theme) => statusTone(theme, "info").text,
              bgcolor: (theme) => statusTone(theme, "info").bg,
              font: "inherit",
              fontWeight: 700,
              cursor: "pointer",
              "&:hover": {
                textDecoration: "underline",
              },
            }}
          >
            {part.text}
          </Box>
        ) : part.type === "scope" ? (
          <Box
            key={`${part.text}-${index}`}
            component="span"
            sx={{
              px: 0.35,
              py: 0.1,
              borderRadius: 0.5,
              color: (theme) => statusTone(theme, "info").text,
              bgcolor: (theme) => statusTone(theme, "info").bg,
              fontWeight: 700,
            }}
          >
            {part.text}
          </Box>
        ) : (
          <span key={`${part.text}-${index}`}>{part.text}</span>
        ),
      )}
    </Typography>
  );
}

HighlightedComment.propTypes = {
  comment: PropTypes.object,
  labels: PropTypes.array,
  onFocusScope: PropTypes.func,
};

function CommentComposer({
  labels = [],
  members = [],
  itemLabel = "Item",
  placeholder = "Comment on this item...",
  submitLabel = "Send",
  onSubmit,
  isPending = false,
  disabled = false,
  canTargetMembers = false,
}) {
  const inputRef = useRef(null);
  const [draft, setDraft] = useState("");
  const [referenceTrigger, setReferenceTrigger] = useState(null);
  const [activeReferenceIndex, setActiveReferenceIndex] = useState(0);

  const mentionableMembers = useMemo(() => uniqueMembers(members), [members]);
  const scopeOptions = useMemo(
    () => [
      {
        id: "item",
        token: "#item",
        label: itemLabel,
        secondary: "Current item",
      },
      ...(labels || []).map((label) => ({
        id: labelId(label),
        token: `#${labelTag(label)}`,
        label: label?.name || "Label",
        secondary: "Label",
      })),
    ],
    [itemLabel, labels],
  );
  const referenceSuggestions = useMemo(() => {
    if (!referenceTrigger) return [];
    const query = referenceTrigger.query.toLowerCase();
    if (referenceTrigger.type === "scope") {
      return scopeOptions
        .filter((option) => {
          const searchText = scopeSearchText(option);
          return !query || searchText.includes(query);
        })
        .slice(0, MAX_MENTION_SUGGESTIONS);
    }

    return mentionableMembers
      .filter((member) => {
        const searchText = memberSearchText(member);
        return !query || searchText.includes(query);
      })
      .slice(0, MAX_MENTION_SUGGESTIONS);
  }, [referenceTrigger, scopeOptions, mentionableMembers]);
  const mentionedUserIds = useMemo(
    () => mentionedIdsFromDraft(draft, mentionableMembers),
    [draft, mentionableMembers],
  );
  const targetAnnotatorId = useMemo(
    () =>
      canTargetMembers && mentionedUserIds.length === 1
        ? mentionedUserIds[0]
        : "",
    [canTargetMembers, mentionedUserIds],
  );
  const targetMember = useMemo(
    () =>
      targetAnnotatorId
        ? mentionableMembers.find(
            (member) => String(memberId(member)) === String(targetAnnotatorId),
          )
        : null,
    [mentionableMembers, targetAnnotatorId],
  );
  const scopedLabelId = useMemo(
    () => labelIdFromDraft(draft, labels),
    [draft, labels],
  );
  const canSubmit = draft.trim() && !isPending && !disabled;

  const reset = () => {
    setDraft("");
    setReferenceTrigger(null);
    setActiveReferenceIndex(0);
  };

  const handleDraftChange = (event) => {
    const value = event.target.value;
    const caretIndex = event.target.selectionStart ?? value.length;
    setDraft(value);
    setReferenceTrigger(findReferenceTrigger(value, caretIndex));
    setActiveReferenceIndex(0);
  };

  const insertMention = (member) => {
    const displayName = memberLabel(member);
    const nextDraft = insertAtTrigger(
      draft,
      referenceTrigger,
      `@${displayName}`,
    );
    const nextCaret = nextDraft.length;
    setDraft(nextDraft);
    setReferenceTrigger(null);
    setActiveReferenceIndex(0);

    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret, nextCaret);
    }, 0);
  };

  const insertScope = (option) => {
    const nextDraft = insertAtTrigger(draft, referenceTrigger, option.token);
    const nextCaret = nextDraft.length;
    setDraft(nextDraft);
    setReferenceTrigger(null);
    setActiveReferenceIndex(0);
    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret, nextCaret);
    }, 0);
  };

  const insertReference = (option) => {
    if (referenceTrigger?.type === "scope") {
      insertScope(option);
    } else {
      insertMention(option);
    }
  };

  const handleSubmit = () => {
    const comment = draft.trim();
    if (!comment) return;
    onSubmit?.(
      {
        comment,
        labelId: scopedLabelId || undefined,
        targetAnnotatorId: targetAnnotatorId || undefined,
        mentionedUserIds,
      },
      { onSuccess: reset },
    );
  };

  return (
    <Stack spacing={1}>
      <TextField
        fullWidth
        multiline
        minRows={2}
        maxRows={5}
        size="small"
        label="Comment"
        placeholder={placeholder}
        helperText={`Type # to reference this item or a label, @ to mention people. Cmd/Ctrl+Enter sends.`}
        value={draft}
        inputRef={inputRef}
        onChange={handleDraftChange}
        sx={{
          "& .MuiOutlinedInput-root": {
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? alpha(theme.palette.common.white, 0.02)
                : theme.palette.background.paper,
            borderRadius: 0.75,
            transition: (theme) =>
              theme.transitions.create(["background-color", "box-shadow"], {
                duration: theme.transitions.duration.shorter,
              }),
            "&.Mui-focused": {
              boxShadow: (theme) =>
                `0 0 0 3px ${alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.18 : 0.12)}`,
            },
          },
          "& .MuiFormHelperText-root": {
            mx: 0,
          },
        }}
        onKeyDown={(event) => {
          if (referenceTrigger) {
            if (event.key === "ArrowDown" && referenceSuggestions.length) {
              event.preventDefault();
              setActiveReferenceIndex(
                (index) => (index + 1) % referenceSuggestions.length,
              );
              return;
            }
            if (event.key === "ArrowUp" && referenceSuggestions.length) {
              event.preventDefault();
              setActiveReferenceIndex((index) =>
                index === 0 ? referenceSuggestions.length - 1 : index - 1,
              );
              return;
            }
            if (
              (event.key === "Enter" && !event.shiftKey) ||
              event.key === "Tab"
            ) {
              if (referenceSuggestions[activeReferenceIndex]) {
                event.preventDefault();
                insertReference(referenceSuggestions[activeReferenceIndex]);
                return;
              }
            }
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setReferenceTrigger(null);
              return;
            }
          }
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            if (canSubmit) handleSubmit();
          }
        }}
      />

      {referenceTrigger && (
        <Paper
          variant="outlined"
          role="listbox"
          aria-label={
            referenceTrigger.type === "scope"
              ? "Reference suggestions"
              : "Mention suggestions"
          }
          sx={{
            maxHeight: 220,
            overflow: "auto",
            bgcolor: "background.paper",
            borderColor: (theme) => neutralBorder(theme, 0.12),
            boxShadow: (theme) =>
              theme.palette.mode === "dark"
                ? `0 16px 36px ${alpha(theme.palette.common.black, 0.38)}`
                : `0 16px 36px ${alpha(theme.palette.grey[500], 0.16)}`,
          }}
        >
          {referenceSuggestions.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 1 }}>
              {referenceTrigger.type === "scope"
                ? "No matching references"
                : "No matching people"}
            </Typography>
          ) : (
            <List dense disablePadding>
              {referenceSuggestions.map((option, index) => (
                <ListItemButton
                  key={
                    referenceTrigger.type === "scope"
                      ? option.id
                      : memberId(option)
                  }
                  role="option"
                  selected={index === activeReferenceIndex}
                  aria-selected={index === activeReferenceIndex}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveReferenceIndex(index)}
                  onClick={() => insertReference(option)}
                  sx={{
                    mx: 0.5,
                    my: 0.25,
                    py: 0.75,
                    borderRadius: 0.75,
                    "&.Mui-selected": {
                      bgcolor: (theme) => quietSurface(theme, 0.06),
                      "&:hover": {
                        bgcolor: (theme) => quietSurface(theme, 0.08),
                      },
                    },
                  }}
                >
                  {referenceTrigger.type === "person" && (
                    <ListItemAvatar sx={{ minWidth: 36 }}>
                      <Avatar
                        sx={{
                          width: 26,
                          height: 26,
                          fontSize: 12,
                          bgcolor: (theme) => quietSurface(theme, 0.08),
                          color: "text.secondary",
                        }}
                      >
                        {memberInitial(option)}
                      </Avatar>
                    </ListItemAvatar>
                  )}
                  <ListItemText
                    primary={
                      referenceTrigger.type === "scope"
                        ? option.token
                        : `@${memberLabel(option)}`
                    }
                    secondary={
                      referenceTrigger.type === "scope"
                        ? `${option.secondary} · ${option.label}`
                        : option.email
                    }
                    primaryTypographyProps={{ variant: "body2", noWrap: true }}
                    secondaryTypographyProps={{
                      variant: "caption",
                      noWrap: true,
                    }}
                  />
                </ListItemButton>
              ))}
            </List>
          )}
        </Paper>
      )}

      <Stack direction="row" alignItems="center" spacing={1}>
        {scopedLabelId && (
          <Chip
            size="small"
            variant="outlined"
            label={
              (labels || []).find((label) => labelId(label) === scopedLabelId)
                ?.name || "Label"
            }
            sx={(theme) => neutralChipSx(theme)}
          />
        )}
        {mentionedUserIds.length > 0 && (
          <Chip
            size="small"
            variant="outlined"
            label={`${mentionedUserIds.length} mentioned`}
            sx={(theme) => statusChipSx("info")(theme)}
          />
        )}
        {targetMember && (
          <Chip
            size="small"
            variant="outlined"
            label={`For @${memberLabel(targetMember)}`}
            sx={(theme) => statusChipSx("warning")(theme)}
          />
        )}
        <LoadingButton
          size="small"
          variant="contained"
          onClick={handleSubmit}
          loading={Boolean(isPending)}
          loadingPosition="start"
          disabled={!canSubmit}
          startIcon={<Iconify icon="eva:paper-plane-fill" width={16} />}
          sx={{
            ml: "auto",
            borderRadius: 0.75,
            bgcolor: "text.primary",
            color: "background.paper",
            boxShadow: "none",
            fontWeight: 700,
            "&:hover": {
              bgcolor: "text.primary",
              boxShadow: (theme) =>
                `0 8px 18px ${alpha(theme.palette.text.primary, 0.16)}`,
            },
          }}
        >
          {submitLabel}
        </LoadingButton>
      </Stack>
    </Stack>
  );
}

CommentComposer.propTypes = {
  labels: PropTypes.array,
  members: PropTypes.array,
  itemLabel: PropTypes.string,
  placeholder: PropTypes.string,
  submitLabel: PropTypes.string,
  onSubmit: PropTypes.func,
  isPending: PropTypes.bool,
  disabled: PropTypes.bool,
  canTargetMembers: PropTypes.bool,
};

function EmojiReactionPicker({ anchorEl, open, onClose, onSelect }) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const customEmoji = query.trim();
  const customEmojiExists = EMOJI_REACTION_OPTIONS.some(
    (option) => option.emoji === customEmoji,
  );
  const canUseCustomEmoji =
    isReactionEmoji(customEmoji) &&
    !customEmojiExists &&
    Boolean(normalizedQuery);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const visibleGroups = useMemo(() => {
    if (!normalizedQuery) return EMOJI_REACTION_GROUPS;
    return EMOJI_REACTION_GROUPS.map((group) => ({
      ...group,
      options: group.options.filter(
        ([emoji, label]) =>
          emoji.includes(customEmoji) ||
          label.toLowerCase().includes(normalizedQuery) ||
          group.title.toLowerCase().includes(normalizedQuery),
      ),
    })).filter((group) => group.options.length > 0);
  }, [customEmoji, normalizedQuery]);

  const selectEmoji = (emoji) => {
    onSelect?.(emoji);
    onClose?.();
  };

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      PaperProps={{
        sx: {
          mt: 0.75,
          width: 304,
          maxWidth: "calc(100vw - 24px)",
          borderRadius: 1,
          border: 1,
          borderColor: "divider",
          bgcolor: "background.paper",
          boxShadow: (theme) =>
            theme.palette.mode === "dark"
              ? `0 18px 42px ${alpha(theme.palette.common.black, 0.46)}`
              : `0 18px 42px ${alpha(theme.palette.grey[500], 0.18)}`,
          overflow: "hidden",
        },
      }}
    >
      <Stack spacing={1} sx={{ p: 1.25 }}>
        <TextField
          autoFocus
          fullWidth
          size="small"
          value={query}
          placeholder="Search or paste emoji"
          onChange={(event) => setQuery(event.target.value)}
          inputProps={{ "aria-label": "Search emoji reactions" }}
        />

        {!normalizedQuery && (
          <Box>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mb: 0.5, fontWeight: 700 }}
            >
              Quick reactions
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.5}>
              {QUICK_REACTION_OPTIONS.map((emoji) => (
                <IconButton
                  key={emoji}
                  size="small"
                  aria-label={`React with ${emoji}`}
                  onClick={() => selectEmoji(emoji)}
                  sx={{
                    width: 34,
                    height: 34,
                    borderRadius: 0.75,
                    border: (theme) =>
                      `1px solid ${neutralBorder(theme, 0.08)}`,
                    bgcolor: (theme) => quietSurface(theme, 0.025),
                    fontSize: 18,
                    "&:hover": {
                      bgcolor: (theme) => quietSurface(theme, 0.07),
                    },
                  }}
                >
                  {emoji}
                </IconButton>
              ))}
            </Stack>
          </Box>
        )}

        <Box sx={{ maxHeight: 286, overflow: "auto", pr: 0.25 }}>
          {canUseCustomEmoji && (
            <Button
              fullWidth
              size="small"
              color="inherit"
              variant="outlined"
              onClick={() => selectEmoji(customEmoji)}
              sx={{
                justifyContent: "flex-start",
                mb: 1,
                borderRadius: 0.75,
                borderColor: (theme) => neutralBorder(theme, 0.12),
                bgcolor: (theme) => quietSurface(theme, 0.025),
              }}
            >
              Use {customEmoji}
            </Button>
          )}

          {visibleGroups.length === 0 && !canUseCustomEmoji ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 1 }}>
              No emoji found.
            </Typography>
          ) : (
            visibleGroups.map((group) => (
              <Box key={group.title} sx={{ mb: 1 }}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mb: 0.5, fontWeight: 700 }}
                >
                  {group.title}
                </Typography>
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "repeat(6, 1fr)",
                    gap: 0.5,
                  }}
                >
                  {group.options.map(([emoji, label]) => (
                    <Tooltip key={`${group.title}-${emoji}`} title={label}>
                      <IconButton
                        size="small"
                        aria-label={`React with ${emoji} ${label}`}
                        onClick={() => selectEmoji(emoji)}
                        sx={{
                          width: 34,
                          height: 34,
                          borderRadius: 0.75,
                          fontSize: 18,
                          color: "text.primary",
                          "&:hover": {
                            bgcolor: (theme) => quietSurface(theme, 0.07),
                          },
                        }}
                      >
                        {emoji}
                      </IconButton>
                    </Tooltip>
                  ))}
                </Box>
              </Box>
            ))
          )}
        </Box>
      </Stack>
    </Popover>
  );
}

EmojiReactionPicker.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onSelect: PropTypes.func,
};

function ReactionBar({ comment, onReact, disabled = false }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const reactions = comment?.reactions || [];
  const sortedReactions = [...reactions].sort((a, b) =>
    String(a?.emoji || "").localeCompare(String(b?.emoji || "")),
  );
  const pickerOpen = Boolean(anchorEl);
  const canReact = !disabled && Boolean(comment?.id);
  const isUpdating = disabled && Boolean(comment?.id);

  const handleReact = (emoji) => {
    if (!canReact) return;
    onReact?.(comment, emoji);
  };

  return (
    <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 0.75 }}>
      {sortedReactions.map((reaction) => {
        const emoji = reaction?.emoji;
        if (!emoji) return null;
        const active = Boolean(reaction?.reacted_by_current_user);
        const count = reactionCount(reaction);
        return (
          <Button
            key={emoji}
            size="small"
            variant="outlined"
            color="inherit"
            disabled={!canReact}
            onClick={() => handleReact(emoji)}
            aria-label={`${emoji} reaction`}
            sx={{
              minWidth: 42,
              height: 28,
              px: 0.75,
              borderRadius: 0.75,
              borderColor: (theme) =>
                active
                  ? alpha(theme.palette.primary.main, 0.34)
                  : neutralBorder(theme, 0.12),
              bgcolor: (theme) =>
                active
                  ? alpha(theme.palette.primary.main, 0.08)
                  : quietSurface(theme, 0.025),
              color: "text.primary",
              boxShadow: "none",
              "&:hover": {
                borderColor: (theme) =>
                  active
                    ? alpha(theme.palette.primary.main, 0.42)
                    : neutralBorder(theme, 0.18),
                bgcolor: (theme) =>
                  active
                    ? alpha(theme.palette.primary.main, 0.12)
                    : quietSurface(theme, 0.06),
              },
            }}
          >
            {emoji}
            {count ? (
              <Box component="span" sx={{ ml: 0.5, fontSize: 12 }}>
                {count}
              </Box>
            ) : null}
          </Button>
        );
      })}
      <Tooltip title={isUpdating ? "Updating reaction" : "Add reaction"}>
        <span>
          <IconButton
            size="small"
            disabled={!canReact}
            aria-label={isUpdating ? "Updating reaction" : "Add reaction"}
            onClick={(event) => setAnchorEl(event.currentTarget)}
            sx={{
              width: 28,
              height: 28,
              borderRadius: 0.75,
              border: (theme) => `1px solid ${neutralBorder(theme, 0.12)}`,
              color: "text.secondary",
              bgcolor: (theme) => quietSurface(theme, 0.015),
              "&:hover": {
                bgcolor: (theme) => quietSurface(theme, 0.06),
                color: "text.primary",
              },
            }}
          >
            {isUpdating ? (
              <CircularProgress size={14} color="inherit" />
            ) : (
              <Iconify icon="solar:smile-circle-outline" width={16} />
            )}
          </IconButton>
        </span>
      </Tooltip>
      <EmojiReactionPicker
        anchorEl={anchorEl}
        open={pickerOpen}
        onClose={() => setAnchorEl(null)}
        onSelect={handleReact}
      />
    </Stack>
  );
}

ReactionBar.propTypes = {
  comment: PropTypes.object,
  onReact: PropTypes.func,
  disabled: PropTypes.bool,
};

function ThreadComment({
  comment,
  labels = [],
  onFocusScope,
  onReact,
  isReacting,
}) {
  const isSystemAction = comment?.action && comment.action !== "comment";
  const timestamp = timelineTimeMeta(comment?.created_at);
  return (
    <Box
      sx={{
        py: 1,
        borderTop: 1,
        borderColor: (theme) => neutralBorder(theme, 0.08),
        "&:first-of-type": { borderTop: 0 },
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Avatar
          sx={{
            width: 28,
            height: 28,
            fontSize: 12,
            bgcolor: (theme) => quietSurface(theme, 0.08),
            color: "text.secondary",
            border: (theme) => `1px solid ${neutralBorder(theme, 0.08)}`,
          }}
        >
          {commentAuthor(comment).slice(0, 1).toUpperCase()}
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography variant="caption" fontWeight={700} noWrap>
              {commentAuthor(comment)}
            </Typography>
            {isSystemAction && (
              <Chip
                size="small"
                label={commentActionLabel(comment.action)}
                sx={(theme) => ({
                  ...neutralChipSx(theme),
                  height: 18,
                  fontSize: 10,
                  textTransform: "capitalize",
                })}
              />
            )}
            {timestamp && (
              <Tooltip title={timestamp.exact}>
                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ ml: "auto", flexShrink: 0 }}
                >
                  {timestamp.relative}
                </Typography>
              </Tooltip>
            )}
          </Stack>
          <HighlightedComment
            comment={comment}
            labels={labels}
            onFocusScope={onFocusScope}
          />
          {!isSystemAction && (
            <ReactionBar
              comment={comment}
              onReact={onReact}
              disabled={isReacting}
            />
          )}
        </Box>
      </Stack>
    </Box>
  );
}

ThreadComment.propTypes = {
  comment: PropTypes.object,
  labels: PropTypes.array,
  onFocusScope: PropTypes.func,
  onReact: PropTypes.func,
  isReacting: PropTypes.bool,
};

function DiscussionThreadCard({
  thread,
  queueId,
  itemId,
  labels,
  members,
  itemLabel,
  onReply,
  onResolve,
  onReopen,
  onReact,
  onFocusScope,
  isReplying,
  isResolving,
  isReopening,
  reactingCommentId,
  actionsDisabled = false,
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const resolved = threadIsResolved(thread);
  const meta = threadStatusMeta(thread);
  const comments = thread.comments || [];
  const timelineParts = threadTimelineParts(thread);

  return (
    <Box
      sx={{
        border: 1,
        borderColor: (theme) =>
          neutralBorder(theme, theme.palette.mode === "dark" ? 0.18 : 0.1),
        borderRadius: 0.75,
        bgcolor: (theme) =>
          theme.palette.mode === "dark"
            ? alpha(theme.palette.common.white, 0.022)
            : theme.palette.background.paper,
        boxShadow: (theme) =>
          theme.palette.mode === "dark"
            ? `0 10px 24px ${alpha(theme.palette.common.black, 0.2)}`
            : `0 8px 20px ${alpha(theme.palette.grey[500], 0.07)}`,
        overflow: "hidden",
        transition: (theme) =>
          theme.transitions.create(["border-color", "box-shadow"], {
            duration: theme.transitions.duration.shorter,
          }),
        "&:hover": {
          borderColor: (theme) =>
            neutralBorder(theme, theme.palette.mode === "dark" ? 0.24 : 0.16),
          boxShadow: (theme) =>
            theme.palette.mode === "dark"
              ? `0 12px 28px ${alpha(theme.palette.common.black, 0.24)}`
              : `0 10px 24px ${alpha(theme.palette.grey[500], 0.09)}`,
        },
      }}
    >
      <Box
        sx={{
          px: 1.25,
          py: 1,
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha(theme.palette.common.white, 0.018)
              : alpha(theme.palette.grey[500], 0.03),
          borderBottom: 1,
          borderColor: (theme) => neutralBorder(theme, 0.08),
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Chip
            size="small"
            variant="outlined"
            label={thread.scopeLabel}
            clickable
            onClick={() =>
              onFocusScope?.({
                labelId: thread?.label_id || "",
                targetAnnotatorId: thread?.target_annotator_id || "",
              })
            }
            sx={(theme) => ({ ...neutralChipSx(theme), maxWidth: "70%" })}
          />
          <Chip
            size="small"
            variant="outlined"
            icon={<Iconify icon={meta.icon} width={14} />}
            label={meta.label}
            sx={(theme) => ({
              ...statusChipSx(meta.color)(theme),
              height: 20,
              fontSize: 11,
              ml: "auto",
            })}
          />
        </Stack>
        {timelineParts.length > 0 && (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ display: "block", mt: 0.5 }}
          >
            {timelineParts.map((part, index) => (
              <Fragment key={part.key}>
                {index > 0 ? " · " : ""}
                <Tooltip title={part.exact}>
                  <Box component="span">{part.label}</Box>
                </Tooltip>
              </Fragment>
            ))}
          </Typography>
        )}
      </Box>

      <Box sx={{ px: 1.25 }}>
        {comments.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
            No comments in this thread.
          </Typography>
        ) : (
          comments.map((comment) => (
            <ThreadComment
              key={comment.id || comment.created_at}
              comment={comment}
              labels={labels}
              onFocusScope={onFocusScope}
              onReact={(targetComment, emoji) =>
                onReact?.({
                  queueId,
                  itemId,
                  commentId: targetComment.id,
                  emoji,
                })
              }
              isReacting={
                actionsDisabled ||
                (Boolean(reactingCommentId) &&
                  String(reactingCommentId) === String(comment.id))
              }
            />
          ))
        )}
      </Box>

      <Divider />
      <Stack direction="row" spacing={1} sx={{ p: 1 }}>
        {!resolved && (
          <Button
            size="small"
            variant="text"
            color="inherit"
            disabled={actionsDisabled}
            onClick={() => setReplyOpen((value) => !value)}
            startIcon={<Iconify icon="solar:reply-bold" width={15} />}
            sx={{
              borderRadius: 0.75,
              color: "text.secondary",
              fontWeight: 700,
              "&:hover": {
                color: "text.primary",
                bgcolor: (theme) => quietSurface(theme, 0.05),
              },
            }}
          >
            Reply
          </Button>
        )}
        {resolved ? (
          <Tooltip title="Move this thread back to Open threads">
            <span style={{ marginLeft: "auto" }}>
              <LoadingButton
                size="small"
                variant="outlined"
                onClick={() =>
                  onReopen?.({ queueId, itemId, threadId: thread.id })
                }
                loading={Boolean(isReopening)}
                loadingPosition="start"
                disabled={actionsDisabled || isReopening}
                startIcon={<Iconify icon="solar:restart-bold" width={15} />}
                sx={actionButtonSx("warning")}
              >
                {isReopening ? "Reopening..." : "Reopen thread"}
              </LoadingButton>
            </span>
          </Tooltip>
        ) : (
          <Tooltip title="Move this thread to Resolved">
            <span style={{ marginLeft: "auto" }}>
              <LoadingButton
                size="small"
                variant="outlined"
                onClick={() =>
                  onResolve?.({ queueId, itemId, threadId: thread.id })
                }
                loading={Boolean(isResolving)}
                loadingPosition="start"
                disabled={actionsDisabled || isResolving}
                startIcon={
                  <Iconify icon="solar:check-circle-bold" width={15} />
                }
                sx={actionButtonSx("success")}
              >
                {isResolving ? "Resolving..." : "Resolve thread"}
              </LoadingButton>
            </span>
          </Tooltip>
        )}
      </Stack>

      <Collapse in={replyOpen && !resolved} unmountOnExit>
        <Box sx={{ px: 1, pb: 1 }}>
          <CommentComposer
            labels={labels}
            members={members}
            itemLabel={itemLabel}
            placeholder="Reply in this thread. Use @person to notify them."
            submitLabel="Reply"
            isPending={isReplying}
            disabled={!queueId || !itemId || actionsDisabled}
            onSubmit={(payload, options) =>
              onReply?.(
                {
                  ...payload,
                  queueId,
                  itemId,
                  threadId: thread.id,
                },
                {
                  onSuccess: () => {
                    options?.onSuccess?.();
                    setReplyOpen(false);
                  },
                },
              )
            }
          />
        </Box>
      </Collapse>
    </Box>
  );
}

DiscussionThreadCard.propTypes = {
  thread: PropTypes.object.isRequired,
  queueId: PropTypes.string,
  itemId: PropTypes.string,
  labels: PropTypes.array,
  members: PropTypes.array,
  itemLabel: PropTypes.string,
  onReply: PropTypes.func,
  onResolve: PropTypes.func,
  onReopen: PropTypes.func,
  onReact: PropTypes.func,
  onFocusScope: PropTypes.func,
  isReplying: PropTypes.bool,
  isResolving: PropTypes.bool,
  isReopening: PropTypes.bool,
  reactingCommentId: PropTypes.string,
  actionsDisabled: PropTypes.bool,
};

export function CollaborationDrawer({
  open,
  onClose,
  queueId,
  itemId,
  itemLabel = "Item",
  labels = [],
  members = [],
  comments = [],
  threads = [],
  canTargetMembers = false,
  canComment = true,
  onFocusScope,
}) {
  const {
    mutate: createComment,
    isPending: isCreating,
    variables: createVariables,
  } = useCreateDiscussionComment();
  const {
    mutate: resolveThread,
    isPending: isResolving,
    variables: resolveVariables,
  } = useResolveDiscussionThread();
  const {
    mutate: reopenThread,
    isPending: isReopening,
    variables: reopenVariables,
  } = useReopenDiscussionThread();
  const {
    mutate: toggleReaction,
    isPending: isReacting,
    variables: reactionVariables,
  } = useToggleDiscussionReaction();

  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        onClose?.();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  const normalizedThreads = useMemo(
    () => normalizeThreads({ threads, comments, itemLabel }),
    [threads, comments, itemLabel],
  );
  const activeThreads = useMemo(
    () => normalizedThreads.filter((thread) => !threadIsResolved(thread)),
    [normalizedThreads],
  );
  const resolvedThreads = useMemo(
    () => normalizedThreads.filter(threadIsResolved),
    [normalizedThreads],
  );

  const submitRootComment = (payload, options) => {
    createComment(
      {
        queueId,
        itemId,
        ...payload,
      },
      options,
    );
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      variant="persistent"
      PaperProps={{
        sx: {
          width: { xs: "100vw", sm: 420, md: 456 },
          maxWidth: "100vw",
          bgcolor: "background.paper",
          borderLeft: 1,
          borderColor: "divider",
          boxShadow: (theme) =>
            theme.palette.mode === "dark"
              ? `-18px 0 42px ${alpha(theme.palette.common.black, 0.48)}`
              : `-18px 0 42px ${alpha(theme.palette.grey[600], 0.16)}`,
        },
      }}
    >
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 1.1,
            borderBottom: 1,
            borderColor: "divider",
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? alpha(theme.palette.common.white, 0.03)
                : theme.palette.background.paper,
          }}
        >
          <Box
            sx={{
              width: 28,
              height: 28,
              borderRadius: 0.75,
              display: "grid",
              placeItems: "center",
              bgcolor: (theme) => quietSurface(theme, 0.06),
              border: (theme) => `1px solid ${neutralBorder(theme, 0.1)}`,
            }}
          >
            <Iconify icon="solar:chat-round-dots-bold" width={17} />
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" noWrap>
              Comments
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {itemLabel}
            </Typography>
          </Box>
          <Chip
            size="small"
            variant="outlined"
            label={`${activeThreads.length} active`}
            sx={(theme) =>
              activeThreads.length
                ? statusChipSx("info")(theme)
                : neutralChipSx(theme)
            }
          />
          <Tooltip title="Close comments">
            <IconButton
              size="small"
              onClick={onClose}
              aria-label="Close comments"
            >
              <Iconify icon="mingcute:close-line" width={18} />
            </IconButton>
          </Tooltip>
        </Stack>

        <Box
          sx={{
            p: 1.25,
            borderBottom: 1,
            borderColor: "divider",
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? alpha(theme.palette.common.white, 0.012)
                : alpha(theme.palette.grey[500], 0.025),
          }}
        >
          {canComment ? (
            <CommentComposer
              labels={labels}
              members={members}
              itemLabel={itemLabel}
              isPending={isCreating && !createVariables?.threadId}
              disabled={!queueId || !itemId}
              canTargetMembers={canTargetMembers}
              onSubmit={submitRootComment}
            />
          ) : (
            <Typography variant="body2" color="text.secondary">
              Only queue members can comment on this item.
            </Typography>
          )}
        </Box>

        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            p: 1.25,
            bgcolor: (theme) =>
              theme.palette.mode === "dark"
                ? theme.palette.background.default
                : alpha(theme.palette.grey[500], 0.025),
          }}
        >
          <Stack spacing={1.5}>
            <Box>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 1 }}
              >
                <Typography variant="subtitle2" sx={{ flex: 1 }}>
                  Open threads
                </Typography>
                <Chip
                  size="small"
                  variant="outlined"
                  label={activeThreads.length}
                  sx={(theme) =>
                    activeThreads.length
                      ? statusChipSx("info")(theme)
                      : neutralChipSx(theme)
                  }
                />
              </Stack>
              <Stack spacing={1.25}>
                {activeThreads.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No active comments.
                  </Typography>
                ) : (
                  activeThreads.map((thread) => (
                    <DiscussionThreadCard
                      key={thread.id}
                      thread={thread}
                      queueId={queueId}
                      itemId={itemId}
                      labels={labels}
                      members={members}
                      itemLabel={itemLabel}
                      onReply={createComment}
                      onResolve={resolveThread}
                      onReopen={reopenThread}
                      onReact={toggleReaction}
                      onFocusScope={onFocusScope}
                      actionsDisabled={!canComment}
                      isReplying={
                        isCreating &&
                        String(createVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      isResolving={
                        isResolving &&
                        String(resolveVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      isReopening={
                        isReopening &&
                        String(reopenVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      reactingCommentId={
                        isReacting
                          ? String(reactionVariables?.commentId || "")
                          : ""
                      }
                    />
                  ))
                )}
              </Stack>
            </Box>

            <Box>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 1 }}
              >
                <Typography variant="subtitle2" sx={{ flex: 1 }}>
                  Resolved threads
                </Typography>
                <Chip
                  size="small"
                  variant="outlined"
                  label={resolvedThreads.length}
                  sx={(theme) =>
                    resolvedThreads.length
                      ? statusChipSx("success")(theme)
                      : neutralChipSx(theme)
                  }
                />
              </Stack>
              <Stack spacing={1.25}>
                {resolvedThreads.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No resolved comments.
                  </Typography>
                ) : (
                  resolvedThreads.map((thread) => (
                    <DiscussionThreadCard
                      key={thread.id}
                      thread={thread}
                      queueId={queueId}
                      itemId={itemId}
                      labels={labels}
                      members={members}
                      itemLabel={itemLabel}
                      onReply={createComment}
                      onResolve={resolveThread}
                      onReopen={reopenThread}
                      onReact={toggleReaction}
                      onFocusScope={onFocusScope}
                      actionsDisabled={!canComment}
                      isReplying={
                        isCreating &&
                        String(createVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      isResolving={
                        isResolving &&
                        String(resolveVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      isReopening={
                        isReopening &&
                        String(reopenVariables?.threadId || "") ===
                          String(thread.id)
                      }
                      reactingCommentId={
                        isReacting
                          ? String(reactionVariables?.commentId || "")
                          : ""
                      }
                    />
                  ))
                )}
              </Stack>
            </Box>
          </Stack>
        </Box>
      </Stack>
    </Drawer>
  );
}

CollaborationDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  queueId: PropTypes.string,
  itemId: PropTypes.string,
  itemLabel: PropTypes.string,
  labels: PropTypes.array,
  members: PropTypes.array,
  comments: PropTypes.array,
  threads: PropTypes.array,
  canTargetMembers: PropTypes.bool,
  canComment: PropTypes.bool,
  onFocusScope: PropTypes.func,
};

export default function DiscussionPanel({
  queueId,
  itemId,
  labels = [],
  members = [],
  comments = [],
  canTargetMembers = false,
}) {
  const inputRef = useRef(null);
  const [draft, setDraft] = useState("");
  const [labelId, setLabelId] = useState("");
  const [targetAnnotatorId, setTargetAnnotatorId] = useState("");
  const [mentionTrigger, setMentionTrigger] = useState(null);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [search, setSearch] = useState("");
  const { mutate: createComment, isPending } = useCreateDiscussionComment();

  const discussionComments = useMemo(
    () => (comments || []).filter(isDiscussionComment),
    [comments],
  );

  const filteredComments = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return discussionComments;
    return discussionComments.filter((comment) =>
      commentSearchText(comment).includes(query),
    );
  }, [discussionComments, search]);

  const mentionableMembers = useMemo(() => uniqueMembers(members), [members]);
  const targetableMembers = canTargetMembers ? mentionableMembers : [];
  const selectedTargetMember = useMemo(
    () =>
      targetAnnotatorId
        ? mentionableMembers.find(
            (member) => String(memberId(member)) === String(targetAnnotatorId),
          )
        : null,
    [mentionableMembers, targetAnnotatorId],
  );

  const mentionSuggestions = useMemo(() => {
    if (!mentionTrigger) return [];
    const query = mentionTrigger.query.toLowerCase();
    return mentionableMembers
      .filter((member) => {
        const searchText = memberSearchText(member);
        return !query || searchText.includes(query);
      })
      .slice(0, MAX_MENTION_SUGGESTIONS);
  }, [mentionTrigger, mentionableMembers]);

  const mentionedUserIds = useMemo(() => {
    const ids = mentionedIdsFromDraft(draft, mentionableMembers);
    if (targetAnnotatorId && !ids.includes(String(targetAnnotatorId))) {
      ids.push(String(targetAnnotatorId));
    }
    return ids;
  }, [draft, mentionableMembers, targetAnnotatorId]);

  const mentionedMembers = useMemo(
    () =>
      mentionableMembers.filter((member) =>
        mentionedUserIds.includes(String(memberId(member))),
      ),
    [mentionableMembers, mentionedUserIds],
  );

  const canSubmit = draft.trim() && queueId && itemId && !isPending;

  const handleDraftChange = (event) => {
    const value = event.target.value;
    const caretIndex = event.target.selectionStart ?? value.length;
    setDraft(value);
    setMentionTrigger(findMentionTrigger(value, caretIndex));
    setActiveMentionIndex(0);
  };

  const insertMention = (member) => {
    if (!mentionTrigger) return;

    const displayName = memberLabel(member);
    const before = draft.slice(0, mentionTrigger.start);
    const after = draft.slice(mentionTrigger.end);
    const inserted = `@${displayName} `;
    const nextDraft = `${before}${inserted}${after}`;
    const nextCaret = before.length + inserted.length;

    setDraft(nextDraft);
    setMentionTrigger(null);
    setActiveMentionIndex(0);

    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret, nextCaret);
    }, 0);
  };

  const handleSubmit = () => {
    const comment = draft.trim();
    if (!comment) return;
    const payload = {
      queueId,
      itemId,
      comment,
      labelId: labelId || undefined,
      mentionedUserIds,
    };
    if (targetAnnotatorId) {
      payload.targetAnnotatorId = targetAnnotatorId;
    }
    createComment(payload, {
      onSuccess: () => {
        setDraft("");
        setLabelId("");
        setTargetAnnotatorId("");
        setMentionTrigger(null);
        setActiveMentionIndex(0);
        setSearch("");
      },
    });
  };

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 0.75,
        bgcolor: "background.paper",
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1}
        sx={{ px: 1.5, py: 1.25 }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Iconify icon="solar:chat-round-dots-bold" width={18} />
          <Typography variant="subtitle2">Discussion</Typography>
        </Stack>
        <Chip
          size="small"
          variant="outlined"
          label={discussionComments.length}
          sx={(theme) => ({ ...neutralChipSx(theme), minWidth: 30 })}
        />
      </Stack>
      <Divider />

      <Stack spacing={1.25} sx={{ p: 1.5 }}>
        {discussionComments.length > 3 && (
          <TextField
            size="small"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search discussion"
          />
        )}

        <Stack spacing={1}>
          {filteredComments.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No discussion yet.
            </Typography>
          ) : (
            filteredComments.map((comment) => {
              const timestamp = timelineTimeMeta(comment?.created_at);
              return (
                <Box
                  key={comment.id || comment.created_at}
                  sx={(theme) => {
                    const tone = statusTone(theme, "info");
                    return {
                      p: 1,
                      border: 1,
                      borderColor: comment?.target_annotator_id
                        ? tone.border
                        : neutralBorder(theme, 0.1),
                      borderRadius: 0.75,
                      bgcolor: comment?.target_annotator_id
                        ? tone.bg
                        : quietSurface(theme, 0.025),
                    };
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <Typography variant="caption" color="text.secondary">
                      {commentAuthor(comment)}
                    </Typography>
                    <Chip
                      size="small"
                      variant="outlined"
                      label={commentScope(comment)}
                      sx={(theme) => ({
                        ...neutralChipSx(theme),
                        height: 18,
                        fontSize: 10,
                      })}
                    />
                    {timestamp && (
                      <Tooltip title={timestamp.exact}>
                        <Typography
                          variant="caption"
                          color="text.disabled"
                          sx={{ ml: "auto", flexShrink: 0 }}
                        >
                          {timestamp.relative}
                        </Typography>
                      </Tooltip>
                    )}
                  </Stack>
                  <HighlightedComment comment={comment} labels={labels} />
                  {(comment.mentioned_users || []).length > 0 && (
                    <Stack
                      direction="row"
                      flexWrap="wrap"
                      gap={0.5}
                      sx={{ mt: 1 }}
                    >
                      {comment.mentioned_users.map((user) => (
                        <Chip
                          key={user.id}
                          size="small"
                          label={`@${memberLabel(user)}`}
                          variant="outlined"
                          sx={(theme) => statusChipSx("info")(theme)}
                        />
                      ))}
                    </Stack>
                  )}
                </Box>
              );
            })
          )}
        </Stack>

        <Divider />

        <Box>
          <Typography
            variant="caption"
            fontWeight={600}
            color="text.secondary"
            sx={{ display: "block", mb: 0.75 }}
          >
            Comment on
          </Typography>
          <Stack
            role="group"
            aria-label="Comment scope"
            direction="row"
            flexWrap="wrap"
            gap={0.75}
          >
            <Button
              size="small"
              variant={!labelId ? "contained" : "outlined"}
              color="inherit"
              aria-pressed={!labelId}
              onClick={() => setLabelId("")}
              sx={{ borderRadius: 0.75, minHeight: 30 }}
            >
              Item
            </Button>
            {labels.map((label) => {
              const value = String(label.label_id || label.id);
              const isSelected = String(labelId) === value;
              return (
                <Button
                  key={value}
                  size="small"
                  variant={isSelected ? "contained" : "outlined"}
                  color="inherit"
                  aria-pressed={isSelected}
                  onClick={() => setLabelId(value)}
                  sx={{
                    borderRadius: 0.75,
                    minHeight: 30,
                    maxWidth: "100%",
                  }}
                >
                  {label.name}
                </Button>
              );
            })}
          </Stack>
        </Box>

        {targetableMembers.length > 0 && (
          <Box>
            <Typography
              variant="caption"
              fontWeight={600}
              color="text.secondary"
              sx={{ display: "block", mb: 0.75 }}
            >
              Notify / focus
            </Typography>
            <Stack
              role="group"
              aria-label="Comment audience"
              direction="row"
              flexWrap="wrap"
              gap={0.75}
            >
              <Button
                size="small"
                variant={!targetAnnotatorId ? "contained" : "outlined"}
                color="inherit"
                aria-pressed={!targetAnnotatorId}
                onClick={() => setTargetAnnotatorId("")}
                sx={{ borderRadius: 0.75, minHeight: 30 }}
              >
                Everyone
              </Button>
              {targetableMembers.map((member) => {
                const value = String(memberId(member));
                const isSelected = String(targetAnnotatorId) === value;
                return (
                  <Button
                    key={value}
                    size="small"
                    variant={isSelected ? "contained" : "outlined"}
                    color="inherit"
                    aria-pressed={isSelected}
                    onClick={() => setTargetAnnotatorId(value)}
                    sx={{
                      borderRadius: 0.75,
                      minHeight: 30,
                      maxWidth: "100%",
                      justifyContent: "flex-start",
                    }}
                  >
                    @{memberLabel(member)}
                  </Button>
                );
              })}
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.75 }}
            >
              {selectedTargetMember
                ? `Scoped to ${memberLabel(
                    selectedTargetMember,
                  )}; they are automatically @mentioned and the thread stays focused on them.`
                : "Everyone on the queue can see this comment. Type @ to notify a specific person."}
            </Typography>
          </Box>
        )}

        <Box>
          <TextField
            fullWidth
            multiline
            minRows={2}
            maxRows={5}
            size="small"
            label="Comment"
            helperText={
              selectedTargetMember
                ? `${memberLabel(
                    selectedTargetMember,
                  )} is automatically mentioned. Cmd/Ctrl+Enter sends.`
                : "Type @name or @email to mention and notify a queue member. Cmd/Ctrl+Enter sends."
            }
            value={draft}
            inputRef={inputRef}
            onChange={handleDraftChange}
            onKeyDown={(event) => {
              if (mentionTrigger) {
                if (event.key === "ArrowDown" && mentionSuggestions.length) {
                  event.preventDefault();
                  setActiveMentionIndex(
                    (index) => (index + 1) % mentionSuggestions.length,
                  );
                  return;
                }
                if (event.key === "ArrowUp" && mentionSuggestions.length) {
                  event.preventDefault();
                  setActiveMentionIndex((index) =>
                    index === 0 ? mentionSuggestions.length - 1 : index - 1,
                  );
                  return;
                }
                if (
                  (event.key === "Enter" && !event.shiftKey) ||
                  event.key === "Tab"
                ) {
                  if (mentionSuggestions[activeMentionIndex]) {
                    event.preventDefault();
                    insertMention(mentionSuggestions[activeMentionIndex]);
                    return;
                  }
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  setMentionTrigger(null);
                  return;
                }
              }
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                if (canSubmit) handleSubmit();
              }
            }}
          />

          {mentionTrigger && (
            <Paper
              variant="outlined"
              role="listbox"
              aria-label="Mention suggestions"
              sx={{
                mt: 0.5,
                maxHeight: 240,
                overflow: "auto",
                bgcolor: "background.paper",
              }}
            >
              {mentionSuggestions.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ p: 1 }}
                >
                  No matching people
                </Typography>
              ) : (
                <List dense disablePadding>
                  {mentionSuggestions.map((member, index) => (
                    <ListItemButton
                      key={memberId(member)}
                      role="option"
                      selected={index === activeMentionIndex}
                      aria-selected={index === activeMentionIndex}
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseEnter={() => setActiveMentionIndex(index)}
                      onClick={() => insertMention(member)}
                      sx={{ py: 0.75 }}
                    >
                      <ListItemAvatar sx={{ minWidth: 36 }}>
                        <Avatar sx={{ width: 26, height: 26, fontSize: 12 }}>
                          {memberInitial(member)}
                        </Avatar>
                      </ListItemAvatar>
                      <ListItemText
                        primary={`@${memberLabel(member)}`}
                        secondary={member.email}
                        primaryTypographyProps={{
                          variant: "body2",
                          noWrap: true,
                        }}
                        secondaryTypographyProps={{
                          variant: "caption",
                          noWrap: true,
                        }}
                      />
                    </ListItemButton>
                  ))}
                </List>
              )}
            </Paper>
          )}

          {mentionedMembers.length > 0 && (
            <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 1 }}>
              {mentionedMembers.map((member) => (
                <Chip
                  key={memberId(member)}
                  size="small"
                  label={`@${memberLabel(member)}`}
                  variant={
                    String(memberId(member)) === String(targetAnnotatorId)
                      ? "soft"
                      : "outlined"
                  }
                  color={
                    String(memberId(member)) === String(targetAnnotatorId)
                      ? "info"
                      : "default"
                  }
                  sx={{ height: 22 }}
                />
              ))}
            </Stack>
          )}
        </Box>

        <LoadingButton
          variant="outlined"
          size="small"
          onClick={handleSubmit}
          loading={Boolean(isPending)}
          loadingPosition="start"
          disabled={!canSubmit}
          startIcon={<Iconify icon="eva:paper-plane-fill" width={16} />}
        >
          Add comment
        </LoadingButton>
      </Stack>
    </Box>
  );
}

DiscussionPanel.propTypes = {
  queueId: PropTypes.string,
  itemId: PropTypes.string,
  labels: PropTypes.array,
  members: PropTypes.array,
  comments: PropTypes.array,
  canTargetMembers: PropTypes.bool,
};
