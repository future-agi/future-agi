import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useHubMe, useMyLikes } from "./api/queries";
import { EntryGrid } from "./components/EntryCard";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import HubPanel from "./components/HubPanel";
import {
  HubError,
  HubSkeletonCards,
  HubSkeletonPage,
} from "./components/HubStates";
import {
  CreatePublisher,
  PublisherProfile,
  problemText,
} from "./components/PublisherSettings";
import TokenSettings from "./components/TokenSettings";

// Hub settings (SPEC §7.1): my publisher profile, Hub tokens, the
// verification request, and my likes (visible only to me). Hub tokens show
// only once a customer can install a CLI with `omega hub` (OMEGA_PART
// HUB_CLI): a token's only use is `omega hub login`, and the server refuses
// to make one before then. The publishers a person already acts for come
// first; a new user publisher (one per person) is a form that opens at once
// for someone with none, under the section's own title, and behind a button
// for everyone else.

const NO_ANSWER = "The Hub registry did not answer; try again in a moment.";

const publishersTitle = (count) => {
  if (!count) return "Create your publisher";
  return count > 1 ? "Your publishers" : "Your publisher";
};

function PublishersSection({ publishers }) {
  const [creating, setCreating] = useState(false);
  const opener = useRef(null);
  const formId = useId();
  const hasAny = publishers.length > 0;
  const canCreate = !publishers.some((p) => p.kind === "user");
  const showForm = canCreate && (!hasAny || creating);
  const close = () => {
    setCreating(false);
    opener.current?.focus();
  };
  return (
    <HubPanel
      title={publishersTitle(publishers.length)}
      testId="hub-publishers-settings"
      description={
        hasAny
          ? "Who your entries are published as. A profile shows on its publisher page and on each of its entries."
          : "Your entries are published as handle/name. A handle can never be reused once taken, and trust words (official, verified and the like) are refused."
      }
      action={
        canCreate && hasAny ? (
          <Button
            ref={opener}
            size="small"
            variant="outlined"
            aria-expanded={creating}
            aria-controls={creating ? formId : undefined}
            onClick={() => (creating ? close() : setCreating(true))}
            data-testid="hub-create-publisher-open"
          >
            Create your own publisher
          </Button>
        ) : null
      }
    >
      {publishers.map((p) => (
        <PublisherProfile key={p.handle} publisher={p} />
      ))}
      {showForm && (
        <CreatePublisher
          id={formId}
          autoFocus={hasAny}
          divided={hasAny}
          titled={hasAny}
          onCancel={hasAny ? close : undefined}
        />
      )}
    </HubPanel>
  );
}

PublishersSection.propTypes = { publishers: PropTypes.array.isRequired };

// A list that did not load, or did not refresh, says so with Try again.
function ListFailed({ children, onRetry }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 1.5,
      }}
    >
      <Typography typography="s1" color="text.secondary">
        {children}
      </Typography>
      <Button size="small" variant="outlined" onClick={onRetry}>
        Try again
      </Button>
    </Box>
  );
}

ListFailed.propTypes = {
  children: PropTypes.node,
  onRetry: PropTypes.func.isRequired,
};

// The entries stay in view when a later refresh fails; the failure shows
// above them.
function LikesSection({ likes }) {
  const rows = likes.data || [];
  const loadProblem = problemText(likes.error, NO_ANSWER);
  return (
    <HubPanel
      title="Entries you liked"
      testId="hub-my-likes"
      description="Only you see this list. The Hub shows each entry's count, never who liked it."
    >
      {likes.isLoading ? (
        <HubSkeletonCards count={3} />
      ) : rows.length ? (
        <>
          {likes.isError && (
            <ListFailed onRetry={() => likes.refetch()}>
              {`The entries you liked did not refresh and may be out of date. ${loadProblem}`}
            </ListFailed>
          )}
          <EntryGrid entries={rows} />
        </>
      ) : likes.isError ? (
        <ListFailed onRetry={() => likes.refetch()}>
          {`The entries you liked did not load. ${loadProblem}`}
        </ListFailed>
      ) : (
        <Typography typography="s1" color="text.tertiary">
          You have not liked any entries. Like one from its page and it shows
          here.{" "}
          <Link component={RouterLink} href={hubPaths.browse}>
            Browse the Hub
          </Link>
        </Typography>
      )}
    </HubPanel>
  );
}

LikesSection.propTypes = { likes: PropTypes.object.isRequired };

export default function HubSettingsPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const me = useHubMe();
  const likes = useMyLikes();
  const publishers = me.data?.publishers || [];
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
      nav={<HubNav />}
    >
      {me.isLoading ? (
        <HubSkeletonPage />
      ) : me.isError ? (
        <HubError error={me.error} onRetry={() => me.refetch()} />
      ) : (
        <>
          <PublishersSection publishers={publishers} />
          {cliReleased && (
            <TokenSettings
              publishers={publishers.filter((p) => !p.suspended)}
            />
          )}
          <LikesSection likes={likes} />
        </>
      )}
    </HubFrame>
  );
}
