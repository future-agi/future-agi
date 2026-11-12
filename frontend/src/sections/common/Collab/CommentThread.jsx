import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Skeleton, Stack, Typography } from "@mui/material";
import ErrorState from "src/sections/common/ux/ErrorState";
import { notify } from "src/sections/common/ux/toast";
import {
  collabError,
  useDeleteComment,
  usePatchComment,
  usePostComment,
  useRowComments,
} from "src/api/develop/row-collab";
import CommentComposer from "./CommentComposer";
import CommentCard from "./CommentCard";
import { linkSx } from "./collabStyles";
import {
  ALL_RUN_THREADS,
  anchorLabel,
  filterThreads,
  foldLine,
  noOpenText,
  resolvedLine,
} from "./commentModel";

function Thread({ thread, data, mutations, runLabel }) {
  const [replying, setReplying] = useState(false);
  const [unfolded, setUnfolded] = useState(false);
  const { root, replies } = thread;
  const me = data?.me ?? {};
  if (root.resolved && !unfolded)
    return (
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        data-testid="comment-thread-resolved"
        sx={{ py: 0.75, px: 1, borderRadius: 1, bgcolor: "background.neutral" }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ flex: 1, minWidth: 0 }}
        >
          {foldLine(root, me.id, anchorLabel(root.anchor, { runLabel }))}
        </Typography>
        <ButtonBase
          onClick={() => setUnfolded(true)}
          sx={linkSx}
          data-testid="comment-show"
        >
          Show
        </ButtonBase>
        {thread.can_resolve && (
          <ButtonBase
            onClick={() =>
              mutations.patch(root, { resolved: false }, "Thread reopened")
            }
            sx={linkSx}
            data-testid="comment-reopen"
          >
            Reopen
          </ButtonBase>
        )}
      </Stack>
    );
  return (
    <Box
      data-testid="comment-thread"
      sx={{
        py: 1,
        px: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <CommentCard
        comment={root}
        root
        thread={thread}
        data={data}
        runLabel={runLabel}
        mutations={mutations}
        onReply={() => setReplying(true)}
      />
      {replies.length > 0 && (
        <Stack gap={1} sx={{ mt: 1, pl: 3.75 }}>
          {replies.map((r) => (
            <CommentCard
              key={r.id}
              comment={r}
              thread={thread}
              data={data}
              mutations={mutations}
            />
          ))}
        </Stack>
      )}
      {root.resolved && (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ mt: 0.75, pl: 3.75 }}
        >
          <Typography typography="s3" color="text.secondary">
            {resolvedLine(root, me.id)}
          </Typography>
          {thread.can_resolve && (
            <ButtonBase
              onClick={() =>
                mutations.patch(root, { resolved: false }, "Thread reopened")
              }
              sx={linkSx}
              data-testid="comment-reopen"
            >
              Reopen
            </ButtonBase>
          )}
          <ButtonBase onClick={() => setUnfolded(false)} sx={linkSx}>
            Fold
          </ButtonBase>
        </Stack>
      )}
      {replying && (
        <Box sx={{ mt: 1, pl: 3.75 }}>
          <CommentComposer
            limits={data?.limits}
            access={data?.access}
            placeholder="Reply, or type @ to mention someone"
            submitLabel="Reply"
            autoFocus
            testId="comment-reply-composer"
            onCancel={() => setReplying(false)}
            onSubmit={async (body) => {
              const ok = await mutations.post({ body, parent_id: root.id });
              if (ok) setReplying(false);
              return ok;
            }}
          />
        </Box>
      )}
    </Box>
  );
}

Thread.propTypes = {
  thread: PropTypes.object.isRequired,
  data: PropTypes.object,
  mutations: PropTypes.object.isRequired,
  runLabel: PropTypes.string,
};

/**
 * W4-1: the comment threads of one dataset row, or of one result row
 * (row × run when `runId` is set), with the composer pinned at the bottom.
 * `includeRuns` lists the row's result-row threads too (each with its run
 * in the anchor chip); the composer still starts row threads.
 * `status` = `open` · `resolved` · `all` (the Activity sub-toggle).
 * `anchorOptions` = `[{key, label, anchor}]` (Whole row + the row's fields);
 * `stepOptions` = the try's trace steps (result rows).
 */
const CommentThreads = ({
  datasetId,
  rowId,
  runId = null,
  includeRuns = false,
  status = "all",
  anchorOptions,
  stepOptions,
  emptyText = "No comments on this row yet. Comments are visible to people on this dataset.",
  showComposer = true,
  focusComposer = "",
  onComposerFocused,
  composerAnchorKey = "",
}) => {
  const q = useRowComments(datasetId, rowId, {
    runId: includeRuns ? ALL_RUN_THREADS : runId,
  });
  const post = usePostComment(datasetId, rowId);
  const patch = usePatchComment(datasetId, rowId);
  const del = useDeleteComment(datasetId, rowId);
  const data = q.data;
  const threads = useMemo(
    () => filterThreads(data?.threads, status),
    [data, status],
  );

  const mutations = {
    post: async (payload) => {
      try {
        await post.mutateAsync({
          ...payload,
          ...(runId && !includeRuns ? { run_id: runId } : {}),
        });
        return true;
      } catch (e) {
        notify(collabError(e, "Couldn't post the comment."), {
          variant: "error",
        });
        return false;
      }
    },
    patch: async (comment, change, done) => {
      try {
        await patch.mutateAsync({ commentId: comment.id, patch: change });
        if (done) notify(done, { key: `comment-${comment.id}` });
        return true;
      } catch (e) {
        notify(collabError(e, "Couldn't change the comment."), {
          variant: "error",
        });
        return false;
      }
    },
    remove: async (comment) => {
      try {
        await del.mutateAsync(comment.id);
        notify("Comment deleted", { key: `comment-${comment.id}` });
        return true;
      } catch (e) {
        notify(collabError(e, "Couldn't delete the comment."), {
          variant: "error",
        });
        return false;
      }
    },
  };

  if (q.isError)
    return (
      <ErrorState
        thing="the comments"
        error={q.error}
        onRetry={() => q.refetch()}
        compact
      />
    );
  if (q.isLoading)
    return (
      <Stack gap={1}>
        <Skeleton variant="rounded" height={56} />
        <Skeleton variant="rounded" height={56} />
      </Stack>
    );
  const runLabel = data?.run_label ?? null;
  return (
    <Stack gap={1} data-testid="comment-threads">
      {threads.length === 0 ? (
        <Typography typography="s2" color="text.secondary" sx={{ py: 0.5 }}>
          {(status === "open" && noOpenText(data?.threads)) ||
            (status === "resolved" ? "No resolved threads yet." : emptyText)}
        </Typography>
      ) : (
        threads.map((t) => (
          <Thread
            key={t.root.id}
            thread={t}
            data={data}
            mutations={mutations}
            runLabel={t.run_label ?? runLabel}
          />
        ))
      )}
      {showComposer && data?.me?.perms?.view !== false && (
        <Box
          sx={{
            position: "sticky",
            bottom: 0,
            pt: 1,
            pb: 0.5,
            bgcolor: "background.paper",
            zIndex: 1,
          }}
        >
          <CommentComposer
            limits={data?.limits}
            access={data?.access}
            anchorOptions={anchorOptions}
            stepOptions={stepOptions}
            busy={post.isPending}
            focusSignal={focusComposer}
            onFocused={onComposerFocused}
            anchorKey={composerAnchorKey}
            onSubmit={(body, anchor) => mutations.post({ body, anchor })}
          />
        </Box>
      )}
    </Stack>
  );
};

CommentThreads.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  runId: PropTypes.string,
  includeRuns: PropTypes.bool,
  status: PropTypes.oneOf(["open", "resolved", "all"]),
  anchorOptions: PropTypes.array,
  stepOptions: PropTypes.array,
  emptyText: PropTypes.string,
  composerAnchorKey: PropTypes.string,
  showComposer: PropTypes.bool,
  focusComposer: PropTypes.string,
  onComposerFocused: PropTypes.func,
};

export default CommentThreads;
