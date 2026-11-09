import React from "react";
import PropTypes from "prop-types";

import { hubPaths } from "../constants";
import { useHubMe } from "../api/queries";

import HubFrame from "./HubFrame";
import HubNav from "./HubNav";
import { HubError, HubForbidden, HubSkeletonPage } from "./HubStates";

// Review pages render only when /me says isMaintainer (SPEC §7.1); everyone
// else gets the 403 state. The server refuses their calls regardless.
export default function MaintainerGate({ title, description, documentTitle, back, children, testId }) {
  const me = useHubMe();
  if (me.isLoading) {
    return (
      <HubFrame title={title} documentTitle={documentTitle}>
        <HubSkeletonPage />
      </HubFrame>
    );
  }
  if (me.isError && me.error?.status !== 401 && me.error?.status !== 403) {
    return (
      <HubFrame title={title} documentTitle={documentTitle}>
        <HubError error={me.error} onRetry={() => me.refetch()} />
      </HubFrame>
    );
  }
  if (!me.data?.isMaintainer) {
    return (
      <HubFrame documentTitle="No permission" back={{ href: hubPaths.browse, label: "Hub" }} testId="hub-review-forbidden">
        <HubForbidden />
      </HubFrame>
    );
  }
  return (
    <HubFrame title={title} description={description} documentTitle={documentTitle} back={back} testId={testId}>
      <HubNav section="review" />
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
