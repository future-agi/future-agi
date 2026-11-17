// Start review sends the outside reviewers' invites right after it creates
// the queue (W4-SCOPE §8 C3). One toast for the invites.
import { sendInvites } from "src/api/develop/review-links";
import notify from "src/sections/common/ux/toast";
import { inviteBody } from "./inviteModel";

const errorText = (e, fallback) =>
  (typeof e?.result === "string" && e.result) || fallback;

/** Send the Start review form's invites to a new queue; one toast. */
export async function sendOutsideInvites(queueId, value, { metric } = {}) {
  if (!value?.on || !value.emails?.length) return null;
  try {
    const out = await sendInvites(queueId, inviteBody(value, { metric }));
    const n = out?.invites?.length ?? 0;
    notify(
      out?.pending_approval
        ? `${n} outside ${n === 1 ? "invite waits" : "invites wait"} for an admin to approve`
        : `Created ${n} review ${n === 1 ? "link" : "links"} · copy them from the review summary`,
      { variant: "success", key: "outside-invites" },
    );
    return out;
  } catch (e) {
    notify(errorText(e, "Could not create the review links."), {
      variant: "error",
      key: "outside-invites",
    });
    return null;
  }
}
