// What the builder chat holds beyond what one read of the job carries: the
// events before and after the newest page, the messages before the newest, and
// the messages around one far back (`around`), apart from the rest until the
// two touch.
import { useCallback, useMemo, useRef } from "react";
import { isEqual } from "lodash";
import {
  replaceEqualDeep,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { listHarnessChatHistory } from "src/api/harness/harness";

/**
 * The chat items held and the ones just received, as one list in order. The
 * server numbers a conversation's events and its messages one by one, so
 * lists that touch or overlap join. Newer ones that don't touch what is held
 * replace it, so the list never has a gap.
 */
function mergeItems(held = [], incoming = []) {
  if (!held.length) return incoming;
  if (!incoming.length) return held;
  if (incoming[0].sequence > held[held.length - 1].sequence + 1) {
    return incoming;
  }
  if (incoming[incoming.length - 1].sequence < held[0].sequence - 1) {
    return held;
  }
  const bySequence = new Map(
    [...held, ...incoming].map((item) => [item.sequence, item]),
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

const firstMessage = (conversation) =>
  (conversation.older_messages?.[0] ?? conversation.messages?.[0])?.sequence;

// The messages read around one far back become part of the rest once the two
// touch.
function joinAround(conversation) {
  const { around } = conversation;
  const first = firstMessage(conversation);
  if (!around || around.messages.at(-1).sequence < first - 1) {
    return conversation;
  }
  const before = around.messages.filter(({ sequence }) => sequence < first);
  if (!before.length) return { ...conversation, around: null };
  return {
    ...conversation,
    around: null,
    older_messages: [...before, ...(conversation.older_messages ?? [])],
    history: {
      ...conversation.history,
      messages: {
        oldest_sequence: before[0].sequence,
        has_older: around.has_older,
        read_back: true,
      },
    },
  };
}

/**
 * The job as the cache keeps it: every answer, from a poll or a send, joins the
 * chat already held for its conversation. `older_messages`, `around` and
 * `read_back` are this side's own, never sent by the server.
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
  const events = mergeItems(before.events, conversation.events);
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
    conversation: joinAround({
      ...conversation,
      events,
      older_messages,
      around: conversation.around ?? before.around,
      history: {
        messages: start(messagesStartHere, "messages"),
        events: start(eventsStartHere, "events"),
      },
    }),
  });
}

const holdsMessage = (conversation, id) =>
  [
    conversation?.around?.messages,
    conversation?.older_messages,
    conversation?.messages,
  ].some((list) => list?.some((message) => message.message_id === id));

const startOf = (page) => ({
  oldest_sequence: page.oldest_sequence,
  has_older: page.has_older,
  read_back: true,
});

// What each read of the chat's history asks for, given the chat held, and what
// the chat holds once it answers.
const READS = {
  events: {
    params: ({ events }) => ({ kind: "events", before: events?.[0]?.sequence }),
    join: ({ events, history }, page) => ({
      events: mergeItems(page.events, events),
      history: { ...history, events: startOf(page) },
    }),
  },
  messages: {
    params: (held) => ({ kind: "messages", before: firstMessage(held) }),
    join: ({ older_messages, history }, page) => ({
      older_messages: [...page.messages, ...(older_messages ?? [])],
      history: { ...history, messages: startOf(page) },
    }),
  },
  // The messages around one, in place of any other read that way.
  open: {
    params: (held, messageId) => ({ kind: "messages", around: messageId }),
    join: (held, { messages, has_older }) => ({
      around: { messages, has_older },
    }),
  },
  // The ones before those.
  earlier: {
    params: ({ around }) => ({
      kind: "messages",
      before: around?.messages[0].sequence,
    }),
    join: ({ around }, { messages, has_older }) => ({
      around: { messages: mergeItems(messages, around.messages), has_older },
    }),
  },
  // And after: the server reads no page after a message, only around one.
  later: {
    params: ({ around }) => ({
      kind: "messages",
      around: around?.messages.at(-1).message_id,
    }),
    join: ({ around }, { messages }) => ({
      around: {
        ...around,
        messages: mergeItems(around.messages, messages),
      },
    }),
  },
};

/** Reading the rest of the chat a page at a time, one of `READS`. */
export function useChatRead(read, jobId, conversation) {
  const queryClient = useQueryClient();
  const latest = useRef();
  latest.current = { jobId, conversation };
  const { params, join } = READS[read];
  const reading = useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ id, asked }) => listHarnessChatHistory(id, asked),
    onSuccess: (page, { id, asked, messageId, wanted, conversationId }) => {
      queryClient.setQueryData(["harness-job", id], (job) => {
        const held = job?.conversation;
        // Only onto the list it was read for: that starts over when some are missed.
        if (
          !wanted() ||
          held?.conversation_id !== conversationId ||
          !isEqual(params(held, messageId), asked)
        ) {
          return job;
        }
        return { ...job, conversation: { ...held, ...join(held, page) } };
      });
    },
  });
  const { mutateAsync } = reading;
  const load = useCallback(
    (messageId, wanted = () => true) => {
      const { jobId: id, conversation: held } = latest.current;
      const page = mutateAsync({
        id,
        messageId,
        wanted,
        asked: params(held, messageId),
        conversationId: held.conversation_id,
      });
      // A failure shows at the marker, also when the caller doesn't wait for it.
      page.catch(() => {});
      return page;
    },
    [params, mutateAsync],
  );
  const here = reading.variables?.id === jobId;
  const loading = here && reading.isPending;
  const failed = here && reading.isError;
  return useMemo(() => ({ loading, failed, load }), [loading, failed, load]);
}

/** Opening the chat at a message far back: reads the messages around it. */
export function useOpenChatAt(jobId, conversation) {
  const queryClient = useQueryClient();
  const { load } = useChatRead("open", jobId, conversation);
  return useMemo(
    () => ({
      // Whether it is in this chat; rejects when that can't be told.
      open: async (messageId, wanted) => {
        try {
          await load(messageId, wanted);
        } catch (error) {
          if ((error?.statusCode ?? error?.response?.status) === 404)
            return false;
          throw error;
        }
        const job = queryClient.getQueryData(["harness-job", jobId]);
        if (holdsMessage(job?.conversation, messageId)) return true;
        throw new Error("The chat moved on while the message was read.");
      },
      holds: (messageId) => holdsMessage(conversation, messageId),
    }),
    [queryClient, jobId, load, conversation],
  );
}
