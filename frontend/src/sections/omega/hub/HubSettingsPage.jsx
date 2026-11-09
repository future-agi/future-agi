import React from "react";
import Typography from "@mui/material/Typography";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { useHubMe, useMyLikes } from "./api/queries";
import { EntryGrid } from "./components/EntryCard";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import { HubError, HubSection, HubSkeletonPage } from "./components/HubStates";
import {
  CreatePublisher,
  PublisherProfile,
} from "./components/PublisherSettings";
import TokenSettings from "./components/TokenSettings";

// Hub settings (SPEC §7.1): my publisher profile, Hub tokens, the
// verification request, and my likes (visible only to me). Hub tokens show
// only once a customer can install a CLI with `omega hub` (OMEGA_PART
// HUB_CLI): a token's only use is `omega hub login`, and the server refuses
// to make one before then.
export default function HubSettingsPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const me = useHubMe();
  const likes = useMyLikes();
  const publishers = me.data?.publishers || [];
  const own = publishers.filter((p) => p.kind === "user");
  return (
    <HubFrame
      title="Hub settings"
      documentTitle="Hub settings"
      description={
        cliReleased
          ? "Your publisher, your Hub tokens and the entries you liked."
          : "Your publisher and the entries you liked."
      }
      testId="hub-settings"
    >
      <HubNav />
      {me.isLoading ? (
        <HubSkeletonPage />
      ) : me.isError ? (
        <HubError error={me.error} onRetry={() => me.refetch()} />
      ) : (
        <>
          {own.length === 0 && <CreatePublisher />}
          {publishers.map((p) => (
            <PublisherProfile key={p.handle} publisher={p} />
          ))}
          {cliReleased && (
            <TokenSettings
              publishers={publishers.filter((p) => !p.suspended)}
            />
          )}
          <HubSection title="My likes" testId="hub-my-likes">
            <Typography typography="s3" color="text.tertiary">
              {
                "Only you see this list. The Hub shows each entry's count, never who liked it."
              }
            </Typography>
            {(likes.data || []).length ? (
              <EntryGrid entries={likes.data} />
            ) : (
              <Typography typography="s2" color="text.secondary">
                {likes.isLoading
                  ? "Loading…"
                  : "You have not liked anything yet."}
              </Typography>
            )}
          </HubSection>
        </>
      )}
    </HubFrame>
  );
}
