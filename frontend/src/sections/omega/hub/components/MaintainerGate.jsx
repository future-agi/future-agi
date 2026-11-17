import React from "react";
import PropTypes from "prop-types";

import { hubPaths } from "../constants";
import { useHubMe, useReviewQueue } from "../api/queries";

import HubFrame from "./HubFrame";
import HubNav from "./HubNav";
import { HubError, HubForbidden, HubSkeletonPage } from "./HubStates";

// What the Queue tab counts: every submission waiting for a decision. The
// queue page asks the same when no filter narrows it, so the two share one
// request.
const WAITING = { status: "in_review" };

// Review pages render only when /me says isMaintainer (SPEC §7.1); everyone
// else gets the 403 state. The server refuses their calls regardless. The
// Hub's own tab row stays above every review page, so Browse is one click
// away; the review tabs sit under the title, and Queue says how many wait
// (no count at zero).
export default function MaintainerGate({
  title,
  description,
  documentTitle,
  back,
  children,
  testId,
}) {
  const me = useHubMe();
  const waiting = useReviewQueue(WAITING, Boolean(me.data?.isMaintainer));
  if (me.isLoading) {
    return (
      <HubFrame nav={<HubNav />} title={title} documentTitle={documentTitle}>
        <HubSkeletonPage />
      </HubFrame>
    );
  }
  if (me.isError && me.error?.status !== 401 && me.error?.status !== 403) {
    return (
      <HubFrame nav={<HubNav />} title={title} documentTitle={documentTitle}>
        <HubError error={me.error} onRetry={() => me.refetch()} />
      </HubFrame>
    );
  }
  if (!me.data?.isMaintainer) {
    return (
      <HubFrame
        nav={<HubNav />}
        documentTitle="No permission"
        back={{ href: hubPaths.browse, label: "Hub" }}
        testId="hub-review-forbidden"
      >
        <HubForbidden />
      </HubFrame>
    );
  }
  return (
    <HubFrame
      nav={<HubNav />}
      title={title}
      description={description}
      documentTitle={documentTitle}
      back={back}
      testId={testId}
    >
      <HubNav
        section="review"
        counts={{ [hubPaths.review]: waiting.data?.count }}
      />
      {children}
    </HubFrame>
  );
}

MaintainerGate.propTypes = {
  title: PropTypes.node,
  description: PropTypes.node,
  documentTitle: PropTypes.string,
  back: PropTypes.object,
  children: PropTypes.node,
  testId: PropTypes.string,
};
