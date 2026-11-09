import React from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useHubMe, useHubRules } from "./api/queries";
import { useUploadsChecked } from "./lib/uploadsChecked";
import { hubCommand } from "./lib/registry";
import CopyCommand from "./components/CopyCommand";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import {
  HubError,
  HubSection,
  HubSkeletonPage,
  HubState,
} from "./components/HubStates";
import RulesView from "./components/RulesView";
import { StepTracker } from "./components/StatusTimeline";
import UploadCard from "./components/UploadCard";
import UploadsUnavailable from "./components/UploadsUnavailable";

// Publish (SPEC §7.5): from the terminal or by upload, then the pipeline and
// the rules, all rendered from GET /rules. The terminal block shows only once a
// customer can install a CLI with `omega hub` (HUB_CLI). On a server with no Hub
// checker (HUB_PUBLISH false) the page stays and says what is missing in place
// of both ways to publish (UploadsUnavailable; main-session ruling 3).
export default function HubPublishPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const uploads = useUploadsChecked();
  const me = useHubMe();
  const rules = useHubRules();
  const publishers = (me.data?.publishers || []).filter((p) => !p.suspended);

  return (
    <HubFrame
      title="Publish to the Hub"
      documentTitle="Publish"
      description="Every upload is checked automatically, then a Future AGI maintainer reviews it. Nothing is listed until a maintainer approves it."
      testId="hub-publish"
    >
      <HubNav />
      <HubSection title="How publishing works">
        <StepTracker />
        {rules.data?.targets?.review && (
          <Typography typography="s2" color="text.secondary">
            {rules.data.targets.review}
          </Typography>
        )}
      </HubSection>
      {me.isLoading || uploads === "loading" ? (
        <HubSkeletonPage />
      ) : me.isError ? (
        <HubError error={me.error} onRetry={() => me.refetch()} />
      ) : uploads === "unchecked" ? (
        <UploadsUnavailable />
      ) : (
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: {
              xs: "1fr",
              md: cliReleased ? "1fr 1fr" : "1fr",
            },
            alignItems: "start",
          }}
        >
          {cliReleased && (
            <HubSection
              title="Publish from your terminal"
              testId="hub-publish-cli"
            >
              <Typography typography="s2" color="text.secondary">
                Create a Hub token in Settings, then publish a folder. With{" "}
                <Box
                  component="code"
                  sx={{ fontFamily: "monospace", fontSize: 13 }}
                >
                  --wait
                </Box>
                , the command follows it through checks and review.
              </Typography>
              <CopyCommand text={hubCommand(["login"])} />
              <CopyCommand
                text={hubCommand(["publish", "./my-entry", "--wait"])}
              />
              <CopyCommand text={hubCommand(["status"])} />
              <Box>
                <Button
                  component={RouterLink}
                  href={hubPaths.settings}
                  size="small"
                  variant="outlined"
                >
                  Create a Hub token
                </Button>
              </Box>
            </HubSection>
          )}
          {publishers.length ? (
            <UploadCard publishers={publishers} />
          ) : (
            <HubSection title="Upload a folder" testId="hub-upload-card">
              <HubState
                compact
                icon="users"
                title="Create a publisher first"
                description="Entries are published as publisher/name. Pick your handle in Settings; it takes a minute."
                action={
                  <Button
                    component={RouterLink}
                    href={hubPaths.settings}
                    variant="contained"
                    size="small"
                    data-testid="hub-create-publisher-link"
                  >
                    Create a publisher
                  </Button>
                }
              />
            </HubSection>
          )}
        </Box>
      )}
      {rules.isError ? (
        <HubError
          error={rules.error}
          compact
          title="The rules did not load"
          onRetry={() => rules.refetch()}
        />
      ) : (
        <RulesView rules={rules.data} />
      )}
    </HubFrame>
  );
}
