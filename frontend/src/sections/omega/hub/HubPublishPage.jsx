import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useHubMe, useHubRules } from "./api/queries";
import { DISCLOSURE_FIELDS, DISCLOSURE_LENGTHS } from "./lib/disclosure";
import { PIPELINE } from "./lib/status";
import { useUploadsChecked } from "./lib/uploadsChecked";
import { hubCommand } from "./lib/registry";
import CopyCommand from "./components/CopyCommand";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import HubPanel from "./components/HubPanel";
import { HubError, HubSkeletonPage } from "./components/HubStates";
import RulesView from "./components/RulesView";
import UploadCard from "./components/UploadCard";
import UploadsUnavailable from "./components/UploadsUnavailable";

// Publish (SPEC §7.5), in the order a person meets it: what happens after a
// submit, the two ways to submit (the terminal block shows only once a
// customer can run `omega hub`, OMEGA_PART HUB_CLI), the two manifest fields
// to check before every upload, then the rules from GET /rules, folded. On a
// server with no Hub checker (HUB_PUBLISH false) the page stays and
// UploadsUnavailable says what is missing in place of both ways to publish.

// Each caption holds with or without a Hub checker on this server; the
// review time is the rules file's own sentence.
const AFTER_SUBMIT = {
  received:
    "Your folder is stored. Nothing is listed until a maintainer approves it.",
  checks: "Run on every upload. A failed check names the file and the fix.",
  review: "A Future AGI maintainer reads it and confirms its disclosure.",
  published: "Listed on the Hub, with the disclosure the maintainer confirmed.",
};

// What to write in each part of the disclosure; the limits are the checker's
// (lib/disclosure.js mirrors omega_hub/services/disclosure.py).
const DISCLOSURE_GUIDE = {
  summary: "One sentence: what it does and what it calls.",
  network: "What it contacts, without a key and with one.",
  files: "What it reads and writes, and where.",
  shell: "The commands it runs, or None.",
  background: "What keeps running after it answers, or None.",
  credentials: "The keys it reads, and what it stores.",
};

const MANIFEST_FIELDS = [
  {
    key: "version",
    text: "A semver version you have not published for this entry, such as 1.0.0.",
  },
  ...DISCLOSURE_FIELDS.map((field) => {
    const [low, high] = DISCLOSURE_LENGTHS[field];
    return {
      key: `disclosure.${field}`,
      text: `${DISCLOSURE_GUIDE[field]} ${low} to ${high} characters.`,
    };
  }),
];

function Code({ children }) {
  return (
    <Box
      component="code"
      sx={{ fontFamily: "monospace", fontSize: 12, color: "text.primary" }}
    >
      {children}
    </Box>
  );
}

Code.propTypes = { children: PropTypes.node };

function AfterSubmit({ review }) {
  return (
    <Box component="section" aria-labelledby="hub-publish-after-title">
      <Typography
        id="hub-publish-after-title"
        component="h2"
        typography="m3"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
        sx={{ mb: 1.25 }}
      >
        What happens after you submit
      </Typography>
      <Box
        component="ol"
        aria-labelledby="hub-publish-after-title"
        data-testid="hub-publish-pipeline"
        sx={{
          m: 0,
          p: 0,
          listStyle: "none",
          display: "grid",
          columnGap: 2,
          rowGap: 1.5,
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: "repeat(4, minmax(0, 1fr))",
          },
        }}
      >
        {PIPELINE.map((step, index) => (
          <Box
            component="li"
            key={step.key}
            sx={{
              borderTop: "2px solid",
              borderColor: "divider",
              pt: 1,
              minWidth: 0,
            }}
          >
            <Typography typography="s1" fontWeight={500} color="text.primary">
              {`${index + 1}. ${step.label}`}
            </Typography>
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ mt: 0.25 }}
            >
              {AFTER_SUBMIT[step.key]}
            </Typography>
            {step.key === "review" && review && (
              <Typography
                typography="s2"
                fontWeight={500}
                color="text.primary"
                sx={{ mt: 0.25 }}
              >
                {review}
              </Typography>
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

AfterSubmit.propTypes = { review: PropTypes.string };

function Step({ number, title, children }) {
  return (
    <Box
      component="li"
      sx={{
        display: "grid",
        gridTemplateColumns: "24px minmax(0, 1fr)",
        columnGap: 1.5,
        minWidth: 0,
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 24,
          height: 24,
          borderRadius: "50%",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
          color: "text.secondary",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          typography: "s2",
          fontWeight: 600,
        }}
      >
        {number}
      </Box>
      <Box
        sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}
      >
        <Typography
          component="h3"
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
          sx={{ lineHeight: "24px" }}
        >
          {title}
        </Typography>
        {children}
      </Box>
    </Box>
  );
}

Step.propTypes = {
  number: PropTypes.number.isRequired,
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function TerminalSteps() {
  return (
    <HubPanel
      title="Publish from your terminal"
      description="Sign in once on each machine, then publish a folder with one command."
      testId="hub-publish-cli"
    >
      <Box
        component="ol"
        sx={{ m: 0, p: 0, listStyle: "none", display: "grid", gap: 2.5 }}
      >
        <Step number={1} title="Get a Hub token">
          <Typography typography="s1" color="text.secondary">
            Create one in Hub settings for the publisher you publish as. It is
            shown once.
          </Typography>
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
        </Step>
        <Step number={2} title="Sign in on this machine">
          <Typography typography="s1" color="text.secondary">
            The command asks for the token; what you paste stays hidden. In CI,
            add <Code>--token-stdin</Code> to this command and pipe the token
            in.
          </Typography>
          <CopyCommand text={hubCommand(["login"])} />
        </Step>
        <Step number={3} title="Publish the folder">
          <Typography typography="s1" color="text.secondary">
            Put the path to your folder in place of <Code>./my-entry</Code>.
            With <Code>--wait</Code>, the command follows it through the checks
            and review.
          </Typography>
          <CopyCommand text={hubCommand(["publish", "./my-entry", "--wait"])} />
        </Step>
        <Step number={4} title="Follow it later">
          <Typography typography="s1" color="text.secondary">
            Shows where your submission is in the checks and review.{" "}
            <Link component={RouterLink} href={hubPaths.submissions}>
              My submissions
            </Link>{" "}
            shows the same, with each finding.
          </Typography>
          <CopyCommand text={hubCommand(["status"])} />
        </Step>
      </Box>
    </HubPanel>
  );
}

// The two fields to change or check on each upload; the Hub rules list the
// rest of what the checks read. Stacked, beside the upload card in a narrow
// column, each field name sits above its text until the widest screens.
function ManifestFields({ stacked = false }) {
  const wide = stacked ? "lg" : "sm";
  return (
    <HubPanel
      title="Check two fields before every upload"
      description={
        <>
          In <Code>omega.hub.json</Code>, give each upload a version you have
          not published and a disclosure that says what it does. A maintainer
          checks the disclosure against what the automated checks observed and
          confirms it; it then shows on your entry page.
        </>
      }
      testId="hub-publish-manifest"
    >
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            [wide]: "max-content minmax(0, 1fr)",
          },
          columnGap: 3,
          rowGap: { xs: 0.25, [wide]: 1.25 },
        }}
      >
        {MANIFEST_FIELDS.map((field) => (
          <React.Fragment key={field.key}>
            <Box
              component="dt"
              sx={{
                fontFamily: "monospace",
                fontSize: 12,
                lineHeight: "18px",
                color: "text.primary",
                mt: { xs: 1, [wide]: 0 },
              }}
            >
              {field.key}
            </Box>
            <Box
              component="dd"
              sx={{ m: 0, typography: "s1", color: "text.secondary" }}
            >
              {field.text}
            </Box>
          </React.Fragment>
        ))}
      </Box>
    </HubPanel>
  );
}

ManifestFields.propTypes = { stacked: PropTypes.bool };

function CreatePublisherFirst() {
  return (
    <HubPanel title="Upload a folder" testId="hub-upload-card">
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: 1,
        }}
      >
        <Typography
          component="h3"
          typography="s1"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          Create a publisher first
        </Typography>
        <Typography typography="s1" color="text.secondary">
          Entries are published as publisher/name. Pick your handle in Settings;
          it takes a minute.
        </Typography>
        <Button
          component={RouterLink}
          href={hubPaths.settings}
          variant="contained"
          size="small"
          data-testid="hub-create-publisher-link"
        >
          Create a publisher
        </Button>
      </Box>
    </HubPanel>
  );
}

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
      description="Share an agent, team, app, workflow, MCP server, skill or plugin. A Future AGI maintainer approves each version before anyone else sees it."
      testId="hub-publish"
      nav={<HubNav />}
    >
      <AfterSubmit review={rules.data?.targets?.review} />
      {me.isLoading || uploads === "loading" ? (
        <HubSkeletonPage />
      ) : me.isError ? (
        <HubError error={me.error} onRetry={() => me.refetch()} />
      ) : uploads === "unchecked" ? (
        <UploadsUnavailable />
      ) : (
        <>
          <Box
            sx={{
              display: "grid",
              gap: 2,
              // The terminal steps and the upload card share a height; the
              // short "Create a publisher first" card keeps its own.
              alignItems: publishers.length ? "stretch" : "start",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                md: "repeat(2, minmax(0, 1fr))",
              },
            }}
          >
            {cliReleased && <TerminalSteps />}
            {publishers.length ? (
              <UploadCard publishers={publishers} />
            ) : (
              <CreatePublisherFirst />
            )}
            {!cliReleased && <ManifestFields stacked />}
          </Box>
          {cliReleased && <ManifestFields />}
        </>
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
