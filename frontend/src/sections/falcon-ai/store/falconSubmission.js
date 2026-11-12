import useFalconStore from "./useFalconStore";

let nextSubmissionId = 0;

// One recoverable submission spans the initial HTTP create and the socket send.
// The ID is local request metadata, never a header, body field or credential.
export function beginFalconSubmission(text, files, isCurrent = null) {
  const id = `falcon-submission-${++nextSubmissionId}`;
  useFalconStore.setState({
    pendingDraft: {
      id,
      text,
      files: files.map((file) => ({ ...file })),
      isCurrent,
    },
  });
  return id;
}

export function clearFalconSubmission(id) {
  if (id && useFalconStore.getState().pendingDraft?.id === id) {
    useFalconStore.setState({ pendingDraft: null });
  }
}

export function bindFalconSubmission(id, conversationId, clientMessageId) {
  const pending = useFalconStore.getState().pendingDraft;
  if (!pending || pending.id !== id) return false;
  useFalconStore.setState({
    pendingDraft: {
      ...pending,
      conversationId,
      clientMessageId,
      // Creation changed the selection. Socket refusals use both message IDs,
      // and remain recoverable if the user switches conversations meanwhile.
      isCurrent: null,
    },
  });
  return true;
}
