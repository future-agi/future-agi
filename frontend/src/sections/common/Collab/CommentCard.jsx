// W4-1 (Lane B): one comment in a thread: avatar, name, relative time, the
// anchor chip, the Markdown body (mentions in bold), reactions with who
// reacted, and the actions the server allows (Reply · Resolve · Edit ·
// Delete). Editing reuses the composer, so mentions round-trip.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  ButtonBase,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { MarkdownBody } from "src/sections/common/SafeMarkdown/SafeMarkdown";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import CommentComposer from "./CommentComposer";
import { linkSx } from "./collabStyles";
import {
  anchorLabel,
  commentActions,
  markdownOf,
  reactionList,
} from "./commentModel";

const initial = (name) =>
  String(name || "?")
    .trim()
    .charAt(0)
    .toUpperCase() || "?";

export default function CommentCard({
  comment,
  root,
  thread,
  data,
  runLabel,
  onReply,
  mutations,
}) {
  const [editing, setEditing] = useState(false);
  const me = data?.me ?? {};
  const actions = commentActions(comment, {
    meId: me.id,
    canEdit: Boolean(me.perms?.edit),
    isAdmin: Boolean(me.is_admin),
    mentionedInThread: (thread.mention_ids ?? []).includes(me.id),
    canView: Boolean(me.perms?.view ?? true),
  });
  const reactions = reactionList(comment.reactions, data?.limits?.reactions, {
    meId: me.id,
  });
  const anchor = root ? anchorLabel(comment.anchor, { runLabel }) : "";
  // A deleted root keeps its place (and where it was) so the replies under
  // it still read as one conversation, and anyone who can view may reply.
  if (comment.deleted)
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ py: 0.5 }}
        data-testid="comment-deleted"
      >
        <Typography
          typography="s2"
          color="text.disabled"
          sx={{ fontStyle: "italic" }}
        >
          Comment deleted
        </Typography>
        <Typography
          typography="s3"
          color="text.disabled"
          noWrap
          sx={{ flex: 1, minWidth: 0 }}
        >
          {anchor}
        </Typography>
        {root && onReply && me.perms?.view !== false && (
          <ButtonBase onClick={onReply} sx={linkSx} data-testid="comment-reply">
            Reply
          </ButtonBase>
        )}
      </Stack>
    );
  return (
    <Stack
      direction="row"
      gap={1}
      alignItems="flex-start"
      data-testid="comment-card"
    >
      <Avatar
        sx={{ width: 22, height: 22, fontSize: 11, fontWeight: 700, mt: 0.25 }}
      >
        {initial(comment.author?.name)}
      </Avatar>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ flexWrap: "wrap" }}
        >
          <Typography typography="s2" fontWeight={600}>
            {comment.author?.id === me.id ? "You" : comment.author?.name}
          </Typography>
          {anchor && (
            <Box
              component="span"
              data-testid="comment-anchor"
              sx={{
                px: 0.75,
                height: 18,
                display: "inline-flex",
                alignItems: "center",
                borderRadius: 0.5,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
                color: "text.secondary",
                typography: "s3",
                whiteSpace: "nowrap",
              }}
            >
              {anchor}
            </Box>
          )}
          <RelativeTime
            iso={comment.created_at}
            by={comment.author?.name}
            sx={{ typography: "s3", color: "text.disabled" }}
          />
          {comment.edited_at && (
            <Typography typography="s3" color="text.disabled">
              · edited
            </Typography>
          )}
        </Stack>
        {editing ? (
          <Box sx={{ mt: 0.5 }}>
            <CommentComposer
              limits={data?.limits}
              access={data?.access}
              initialBody={comment.body}
              submitLabel="Save"
              autoFocus
              testId="comment-edit"
              onCancel={() => setEditing(false)}
              onSubmit={async (body) => {
                const ok = await mutations.patch(
                  comment,
                  { body },
                  "Comment edited",
                );
                if (ok) setEditing(false);
                return ok;
              }}
            />
          </Box>
        ) : (
          <Box
            sx={{ mt: 0.25, "& p:last-child": { mb: 0 } }}
            data-testid="comment-body"
          >
            {/* HO D8 (W5 C5): the one safe Markdown renderer (no raw HTML,
                safe links, no remote images). */}
            <MarkdownBody text={markdownOf(comment.body)} />
          </Box>
        )}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ mt: 0.25, flexWrap: "wrap" }}
        >
          {reactions.map((r) =>
            r.count > 0 || actions.react ? (
              <Tooltip
                key={r.emoji}
                title={r.count ? r.names.join(", ") : `React ${r.emoji}`}
                placement="top"
              >
                <span>
                  <ButtonBase
                    disabled={!actions.react}
                    onClick={() => mutations.patch(comment, { react: r.emoji })}
                    aria-pressed={r.mine}
                    aria-label={`React ${r.emoji}`}
                    data-testid="comment-reaction"
                    sx={{
                      px: 0.625,
                      height: 20,
                      borderRadius: 2,
                      border: "1px solid",
                      borderColor: r.mine ? "primary.main" : "divider",
                      bgcolor: r.mine ? "action.selected" : "transparent",
                      typography: "s3",
                      gap: 0.375,
                      opacity: r.count ? 1 : 0.55,
                    }}
                  >
                    {r.emoji}
                    {r.count > 0 && <span>{r.count}</span>}
                  </ButtonBase>
                </span>
              </Tooltip>
            ) : null,
          )}
          <Box sx={{ flex: 1 }} />
          {actions.reply && onReply && (
            <ButtonBase
              onClick={onReply}
              sx={linkSx}
              data-testid="comment-reply"
            >
              Reply
            </ButtonBase>
          )}
          {root && actions.resolve && !comment.resolved && (
            <ButtonBase
              onClick={() =>
                mutations.patch(comment, { resolved: true }, "Thread resolved")
              }
              sx={linkSx}
              data-testid="comment-resolve"
            >
              Resolve
            </ButtonBase>
          )}
          {actions.edit && !editing && (
            <ButtonBase
              onClick={() => setEditing(true)}
              sx={linkSx}
              data-testid="comment-edit-open"
            >
              Edit
            </ButtonBase>
          )}
          {actions.remove && (
            <ButtonBase
              onClick={() => mutations.remove(comment)}
              sx={linkSx}
              data-testid="comment-delete"
            >
              Delete
            </ButtonBase>
          )}
        </Stack>
      </Box>
    </Stack>
  );
}

CommentCard.propTypes = {
  comment: PropTypes.object.isRequired,
  root: PropTypes.bool,
  thread: PropTypes.object.isRequired,
  data: PropTypes.object,
  runLabel: PropTypes.string,
  onReply: PropTypes.func,
  mutations: PropTypes.object.isRequired,
};
