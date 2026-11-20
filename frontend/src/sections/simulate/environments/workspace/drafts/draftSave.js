// The Save request for every open draft: each piece with the parent and revision it was read at,
// so a draft that moved since is refused rather than saved blind.
export const draftSaveRequests = (drafts) =>
  (drafts?.pieces ?? []).map((piece) => ({
    piece,
    parentId: drafts[piece].parentId,
    revision: drafts[piece].revision,
  }));
