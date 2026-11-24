// What the builder chat holds beyond what one read of the job carries: the
// events before and after the newest page, and the messages before the newest.
import { useCallback, useRef } from "react";
import {
  replaceEqualDeep,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { listOlderHarnessChat } from "src/api/harness/harness";

/**
 * The chat events held and the ones just received, as one list in order. The
 * server numbers a conversation's events one by one and never changes one, so
 * lists that touch or overlap join. Newer ones that don't touch what is held
 * replace it, so the list never has a gap.
 */
function mergeEvents(held = [], incoming = []) {
  if (!held.length) return incoming;
  if (!incoming.length) return held;
  if (incoming[0].sequence > held[held.length - 1].sequence + 1) {
    return incoming;
  }
  if (incoming[incoming.length - 1].sequence < held[0].sequence - 1) {
    return held;
  }
  const bySequence = new Map(
    [...held, ...incoming].map((event) => [event.sequence, event]),
  );
  return [...bySequence.values()].sort((a, b) => a.sequence - b.sequence);
}

// The messages held that came before the newest page the server sends. Messages
// change in place, so that page itself is always taken as sent. A page that
// doesn't touch the held ones starts over.
function keepOlderMessages(before, conversation) {
  const held = before.messages ?? [];
  const first = conversation.messages?.[0]?.sequence;
  if (!held.length || !first || held.at(-1).sequence < first - 1) return [];
  return [
    ...(before.older_messages ?? []),
    ...held.filter((message) => message.sequence < first),
  ];
}

/**
 * The job as the cache keeps it: every answer, from a poll or a send, joins the
 * chat already held for its conversation. `older_messages` and `read_back` are
 * this side's own, never sent by the server.
 */
export function keepChat(held, job) {
  const conversation = job?.conversation;
  const before = held?.conversation;
  if (
    !before ||
    !conversation ||
    before.conversation_id !== conversation.conversation_id
  ) {
    return replaceEqualDeep(held, job);
  }
  const events = mergeEvents(before.events, conversation.events);
  const older_messages =
    conversation.older_messages ?? keepOlderMessages(before, conversation);
  // Where a list starts is said by the answer that holds its oldest one.
  const eventsStartHere =
    conversation.events?.length &&
    conversation.events[0].sequence === events[0].sequence;
  const messagesStartHere =
    conversation.older_messages || !older_messages.length;
  const start = (here, kind) =>
    (here && conversation.history?.[kind]) || before.history?.[kind];
  return replaceEqualDeep(held, {
    ...job,
    conversation: {
      ...conversation,
      events,
      older_messages,
      history: {
        messages: start(messagesStartHere, "messages"),
        events: start(eventsStartHere, "events"),
      },
    },
  });
}

const firstHeld = {
  events: (conversation) => conversation?.events?.[0]?.sequence,
  messages: (conversation) =>
    (conversation?.older_messages?.[0] ?? conversation?.messages?.[0])
      ?.sequence,
};

const withPage = {
  events: (conversation, page) => ({
    events: mergeEvents(page.events, conversation.events),
  }),
  messages: (conversation, page) => ({
    older_messages: [...page.messages, ...(conversation.older_messages ?? [])],
  }),
};

/** Reading back the chat's `kind` (messages or events), a page at a time. */
export function useOlderChat(kind, jobId, conversation) {
  const queryClient = useQueryClient();
  const latest = useRef();
  latest.current = { jobId, conversation };
  const older = useMutation({
    mutationFn: ({ id, before }) => listOlderHarnessChat(id, { kind, before }),
    onSuccess: (page, { id, before, conversationId }) => {
      queryClient.setQueryData(["harness-job", id], (job) => {
        const held = job?.conversation;
        // Only onto the list it was read for: that starts over when some are missed.
        if (
          held?.conversation_id !== conversationId ||
          firstHeld[kind](held) !== before
        ) {
          return job;
        }
        const start = {
          oldest_sequence: page.oldest_sequence,
          has_older: page.has_older,
          read_back: true,
        };
        return {
          ...job,
          conversation: {
            ...held,
            ...withPage[kind](held, page),
            history: { ...held.history, [kind]: start },
          },
        };
      });
    },
  });
  const { mutateAsync } = older;
  const load = useCallback(() => {
    const { jobId: id, conversation: held } = latest.current;
    const before = firstHeld[kind](held);
    const page = mutateAsync({
      id,
      before,
      conversationId: held.conversation_id,
    });
    // A failure shows at the marker, also when the caller doesn't wait for it.
    page.catch(() => {});
    return page;
  }, [kind, mutateAsync]);
  const here = older.variables?.id === jobId;
  return {
    loading: here && older.isPending,
    failed: here && older.isError,
    load,
  };
}
