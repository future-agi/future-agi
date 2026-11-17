import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { HOST_API } from "src/config-global";
import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { hubApi } from "./api/endpoints";
import {
  useHubEntry,
  useHubMe,
  useHubVersion,
  useHubVersions,
} from "./api/queries";
import { splitRef } from "./lib/format";
import { hubApiUrl } from "./lib/registry";
import { cliCommands } from "./lib/useThis";
import DisclosurePanel from "./components/DisclosurePanel";
import DiscordLink from "./components/DiscordLink";
import EntryFiles from "./components/EntryFiles";
import EntryHeader from "./components/EntryHeader";
import EntryProvenance from "./components/EntryProvenance";
import HubFrame from "./components/HubFrame";
import HubNav from "./components/HubNav";
import { HubError, HubState } from "./components/HubStates";
import InstallsStat from "./components/InstallsStat";
import KeysPanel from "./components/KeysPanel";
import { UnrankedNotice, YankedNotice } from "./components/Notices";
import PublisherCard from "./components/PublisherCard";
import ReadmeView, { isRemoteSrc, splitReadme } from "./components/ReadmeView";
import RemixBlock from "./components/RemixBlock";
import ReportDialog from "./components/ReportDialog";
import RestoreButton from "./components/RestoreButton";
import Tombstone from "./components/Tombstone";
import UseThisMenu from "./components/UseThisMenu";
import VersionsTable from "./components/VersionsTable";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

const TABS = ["card", "files", "versions"];
// The side column's width, which the title block's action box lines up
// with (EntryHeader), and the reading width of the README text.
const SIDE = 340;
const PROSE = 760;
const PAGE_WIDTH = 1400;
const COLUMNS = { xs: "minmax(0, 1fr)", lg: `minmax(0, 1fr) ${SIDE}px` };
// One stack of cards, each the whole width of its column: the side column
// beside the main one, or each part of it on a narrow page.
const STACK = {
  minWidth: 0,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};
const SR_ONLY = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

// The README opens with the entry's own title as a "# " heading; that line
// is left out: the page's heading already says it.
function withoutTitleHeading(markdown, title) {
  if (!markdown || !title) return markdown;
  const match = /^\s*#[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|$)/.exec(markdown);
  return match && match[1].trim() === title.trim()
    ? markdown.slice(match[0].length)
    : markdown;
}

// Placeholder cards, one per row, at the heights the cards usually take.
function SideSkeleton({ heights, testId }) {
  return (
    <Box data-testid={testId} sx={STACK}>
      {heights.map((height) => (
        <Skeleton key={height} variant="rounded" height={height} />
      ))}
    </Box>
  );
}

SideSkeleton.propTypes = {
  heights: PropTypes.arrayOf(PropTypes.number).isRequired,
  testId: PropTypes.string.isRequired,
};

// The page while the entry loads, in the shape it will take: the title
// block and its action box, the main column with its tabs, the side column.
// On a narrow page, the order the page reads in there: What it does, the
// first side cards, the tabs, then the rest of the side cards.
function EntrySkeleton({ narrow = false }) {
  const what = (
    <>
      <Skeleton variant="text" width={120} height={24} />
      <Skeleton variant="rounded" height={72} />
    </>
  );
  const tabs = (
    <>
      <Skeleton variant="rounded" height={36} />
      <Skeleton variant="rounded" height={280} />
    </>
  );
  return (
    <Box
      data-testid="hub-loading"
      aria-busy="true"
      sx={{ display: "flex", flexDirection: "column", gap: 3 }}
    >
      <Box
        data-testid="hub-loading-header"
        sx={{
          display: "grid",
          gap: 3,
          gridTemplateColumns: COLUMNS,
          pb: 3,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <Skeleton variant="text" width={180} />
          <Box sx={{ display: "flex", gap: 2 }}>
            <Skeleton variant="rounded" width={48} height={48} />
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                gap: 0.75,
              }}
            >
              <Skeleton variant="text" width="40%" height={28} />
              <Skeleton variant="text" width="75%" />
              <Skeleton variant="text" width="55%" />
              <Skeleton variant="text" width="35%" />
            </Box>
          </Box>
        </Box>
        <Skeleton variant="rounded" height={narrow ? 88 : 176} />
      </Box>
      {narrow ? (
        <>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
            {what}
          </Box>
          <SideSkeleton heights={[120, 240]} testId="hub-loading-side" />
          <Box
            data-testid="hub-loading-tabs"
            sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
          >
            {tabs}
          </Box>
          <SideSkeleton heights={[200, 112]} testId="hub-loading-side-rest" />
        </>
      ) : (
        <Box
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: COLUMNS,
            alignItems: "start",
          }}
        >
          <Box
            data-testid="hub-loading-tabs"
            sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
          >
            {what}
            {tabs}
          </Box>
          <SideSkeleton heights={[148, 196, 168]} testId="hub-loading-side" />
        </Box>
      )}
    </Box>
  );
}

EntrySkeleton.propTypes = { narrow: PropTypes.bool };

// A tab's content while it loads.
function TabSkeleton() {
  return (
    <Box
      data-testid="hub-loading"
      aria-busy="true"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Skeleton variant="rounded" height={36} />
      {[0, 1, 2, 3].map((row) => (
        <Skeleton key={row} variant="rounded" height={28} />
      ))}
    </Box>
  );
}

// A tab's name with its count in a quiet pill ("Files 8").
function TabLabel({ text, count }) {
  if (!count) return text;
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
    >
      {text}{" "}
      <Box
        component="span"
        sx={{
          px: 0.75,
          borderRadius: 1,
          bgcolor: "background.neutral",
          color: "text.secondary",
          typography: "s3",
          lineHeight: "18px",
        }}
      >
        {count}
      </Box>
    </Box>
  );
}

TabLabel.propTypes = {
  text: PropTypes.string.isRequired,
  count: PropTypes.number,
};

// One entry (SPEC §7.2), laid out like a package page under the Hub's own tab
// row (Browse stays selected). The title block holds the main action (the
// run and add commands, or the download) beside the title, a byline, one
// metadata line, a quiet Like and More. Below, the page reads What it does,
// then the Readme, Files and Versions tabs; the side column reads Use this,
// Keys, Disclosure and Provenance, then the counts once there are any,
// remixes, the publisher and Discord. Below the lg breakpoint (a laptop with
// the sidebar open, a tablet, a phone) there is no room for a side column:
// What it does comes first, then Use this, Keys and Disclosure (what a
// visitor reads before running it), then the tabs, then Provenance and the
// rest; each part is one stack of whole-width cards, so no short card sits
// beside a tall one and the README is not pushed below every card. There is
// no on-page conversation; talk goes to Discord.
export default function HubEntryPage() {
  const { publisher, name, tab: rawTab } = useParams();
  const tab = TABS.includes(rawTab) ? rawTab : "card";
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [reporting, setReporting] = useState(search.get("report") === "1");
  const entry = useHubEntry(publisher, name);
  const me = useHubMe();
  const data = entry.data;
  const latest = data?.latest?.version;
  const wanted = search.get("version") || latest;
  const pinned = useHubVersion(
    publisher,
    name,
    wanted && wanted !== latest ? wanted : null,
  );
  const versions = useHubVersions(publisher, name, Boolean(data));
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("lg"), { noSsr: true });
  const version = wanted && wanted !== latest ? pinned.data : data?.latest;

  // A moved entry: the browser follows the 301, so the answer names another
  // reference; a client that does not follow gets {movedTo}.
  const movedTo =
    entry.error?.movedTo ||
    data?.movedTo ||
    (data?.ref && data.ref !== `${publisher}/${name}` ? data.ref : null);
  useEffect(() => {
    if (!movedTo) return;
    const next = splitRef(movedTo);
    const query = search.toString();
    navigate(
      `${hubPaths.entry(next.publisher, next.name, tab)}${query ? `?${query}` : ""}`,
      { replace: true },
    );
  }, [movedTo, navigate, tab, search]);

  if (entry.isLoading || movedTo) {
    return (
      <HubFrame
        documentTitle={`${publisher}/${name}`}
        maxWidth={PAGE_WIDTH}
        nav={<HubNav />}
      >
        <EntrySkeleton narrow={narrow} />
      </HubFrame>
    );
  }
  // A removed, missing or failed entry keeps the tab row the loading page
  // showed, so the rest of the Hub stays one click away.
  if (entry.error?.tombstone) {
    return (
      <HubFrame
        back={{ href: hubPaths.browse, label: "Hub" }}
        documentTitle={`${publisher}/${name}`}
        nav={<HubNav />}
      >
        <Tombstone tombstone={entry.error.tombstone} />
        {me.data?.isMaintainer && (
          <RestoreButton publisher={publisher} name={name} />
        )}
      </HubFrame>
    );
  }
  if (entry.error?.status === 404) {
    return (
      <HubFrame
        back={{ href: hubPaths.browse, label: "Hub" }}
        documentTitle="Not found"
        nav={<HubNav />}
      >
        <HubState
          icon="search"
          title="No such entry"
          description={`Nothing on the Hub is called ${publisher}/${name}.`}
          testId="hub-not-found"
        />
      </HubFrame>
    );
  }
  if (entry.isError || !data) {
    return (
      <HubFrame
        back={{ href: hubPaths.browse, label: "Hub" }}
        documentTitle={`${publisher}/${name}`}
        nav={<HubNav />}
      >
        <HubError
          title={`${publisher}/${name} did not load`}
          error={entry.error}
          onRetry={() => entry.refetch()}
        />
      </HubFrame>
    );
  }

  const unavailableVersion = [404, 410].includes(pinned.error?.status);
  const signedIn = Boolean(me.data?.user);
  const manifest = version?.manifest || {};
  const commands =
    cliReleased && version ? cliCommands(data.ref, version.version) : null;
  // The approved archive of one version, downloaded in the browser.
  const bundleHref = (number) =>
    `${hubApiUrl(hubApi.versionBundle(publisher, name, number))}?source=web`;
  const downloadHref = version ? bundleHref(version.version) : null;
  const readme = splitReadme(withoutTitleHeading(version?.readme, data.title));
  const readmeCommands = commands
    ? { ...commands, names: [name, data.name, manifest.id].filter(Boolean) }
    : null;
  const query = search.toString();
  const setTab = (next) =>
    navigate(
      `${hubPaths.entry(publisher, name, next)}${query ? `?${query}` : ""}`,
    );
  const versionHref = (next) =>
    `${hubPaths.entry(publisher, name)}${next === latest ? "" : `?version=${encodeURIComponent(next)}`}`;
  const resolveImage = (src) =>
    version && !isRemoteSrc(src)
      ? `${HOST_API || ""}${hubApi.versionFile(publisher, name, version.version, src.replace(/^\.\//, ""))}`
      : null;

  let panel;
  if (tab === "versions") {
    panel = versions.isLoading ? (
      <TabSkeleton />
    ) : versions.isError ? (
      <HubError
        compact
        title="The version list did not load"
        error={versions.error}
        onRetry={() => versions.refetch()}
      />
    ) : (
      <VersionsTable
        versions={versions.data}
        current={version?.version || wanted}
        latest={latest}
        hrefFor={versionHref}
        downloadHrefFor={bundleHref}
      />
    );
  } else if (wanted !== latest && pinned.isError) {
    panel = (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <HubError
          compact
          title={
            pinned.error?.status === 410
              ? "This version was removed"
              : pinned.error?.status === 404
                ? "This version isn't available"
                : `Version ${wanted} did not load`
          }
          error={pinned.error}
          onRetry={unavailableVersion ? undefined : () => pinned.refetch()}
        />
        {unavailableVersion && (
          <Button
            component={RouterLink}
            href={hubPaths.entry(publisher, name)}
            size="small"
            variant="outlined"
          >
            View latest version
          </Button>
        )}
      </Box>
    );
  } else if (!version) {
    panel = <TabSkeleton />;
  } else if (tab === "files") {
    panel = (
      <EntryFiles
        key={version.version}
        publisher={publisher}
        name={name}
        version={version.version}
        files={version.files}
        hasReadme={Boolean(version.readme)}
        versionCount={data.versionCount}
        downloadHref={downloadHref}
        onOpenTab={setTab}
      />
    );
  } else if (readme.rest) {
    panel = (
      <Box sx={{ maxWidth: PROSE }}>
        <ReadmeView
          markdown={readme.rest}
          resolveImage={resolveImage}
          commands={readmeCommands}
          headingOffset={1}
        />
      </Box>
    );
  } else if (version.readme) {
    panel = (
      <Typography typography="s1_2" color="text.secondary">
        The README has nothing beyond what is shown above.
      </Typography>
    );
  } else {
    // No README: say so in the Hub's calm empty state, the same as the Files
    // tab's, and offer the version's files to read instead.
    const hasFiles = version.files?.length > 0;
    panel = (
      <HubState
        compact
        icon="file"
        titleComponent="h3"
        testId="hub-entry-no-readme"
        title="No README"
        description={
          hasFiles
            ? "This version has no README. Its files are on the Files tab."
            : "This version has no README and no files."
        }
        action={
          hasFiles ? (
            <Button
              size="small"
              variant="outlined"
              onClick={() => setTab("files")}
            >
              Open the files
            </Button>
          ) : null
        }
      />
    );
  }

  const intro =
    version && readme.what ? (
      <Box
        component="section"
        aria-labelledby="hub-entry-what-title"
        data-testid="hub-entry-what"
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          maxWidth: PROSE,
        }}
      >
        <Typography
          id="hub-entry-what-title"
          component="h2"
          typography="m2"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
        >
          What it does
        </Typography>
        <ReadmeView
          markdown={readme.what}
          resolveImage={resolveImage}
          commands={readmeCommands}
          headingOffset={1}
          testId="hub-entry-what-body"
        />
      </Box>
    ) : null;

  const tabs = (
    <Box
      component="section"
      aria-labelledby="hub-entry-more-title"
      sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
    >
      <Typography id="hub-entry-more-title" component="h2" sx={SR_ONLY}>
        More about this entry
      </Typography>
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
        <Tabs
          value={tab}
          onChange={(_, next) => setTab(next)}
          aria-label="More about this entry"
          {...TAB_ROW_PROPS}
          sx={{ minWidth: 0, ...TAB_ROW_SCROLL_SX }}
        >
          <Tab
            value="card"
            label="Readme"
            id="hub-entry-tab-card"
            aria-controls="hub-entry-panel"
            data-testid="hub-tab-card"
          />
          <Tab
            value="files"
            label={<TabLabel text="Files" count={version?.files?.length} />}
            id="hub-entry-tab-files"
            aria-controls="hub-entry-panel"
            data-testid="hub-tab-files"
          />
          <Tab
            value="versions"
            label={<TabLabel text="Versions" count={data.versionCount} />}
            id="hub-entry-tab-versions"
            aria-controls="hub-entry-panel"
            data-testid="hub-tab-versions"
          />
        </Tabs>
      </Box>
      <Box
        role="tabpanel"
        id="hub-entry-panel"
        aria-labelledby={`hub-entry-tab-${tab}`}
        sx={{ minWidth: 0 }}
      >
        {panel}
      </Box>
    </Box>
  );

  // The side column in two parts, read in this order. First what a visitor
  // reads before running it: Use this, Keys, Disclosure. On a narrow page
  // each card has the page's width, and Keys and Provenance spread their
  // lists across it.
  const railFirst = version ? (
    <>
      <UseThisMenu
        entryRef={data.ref}
        version={version.version}
        capabilities={version.capabilities}
        models={manifest.models}
      />
      <KeysPanel
        keys={manifest.keys}
        models={manifest.models}
        spread={narrow}
      />
      <DisclosurePanel disclosure={version.disclosure} />
    </>
  ) : null;
  // Then where it came from and who stands behind it.
  const railRest = (
    <>
      {version && (
        <EntryProvenance
          entry={data}
          version={version}
          tier={data.publisher?.tier}
          spread={narrow}
        />
      )}
      {/* InstallsStat frames itself; it waits for a first count, and is not shown where nothing can be counted. */}
      <InstallsStat
        installs30d={data.installs30d}
        opens30d={data.opens30d}
        series={data.installsDaily30}
      />
      <RemixBlock
        publisher={publisher}
        name={name}
        remixOf={data.remixOf}
        remixCount={data.remixCount}
        version={version?.version}
      />
      <PublisherCard publisher={data.publisher} />
      <DiscordLink fullWidth />
    </>
  );

  return (
    <HubFrame
      documentTitle={data.ref}
      testId="hub-entry"
      maxWidth={PAGE_WIDTH}
      nav={<HubNav />}
    >
      <EntryHeader
        entry={data}
        version={version}
        signedIn={signedIn}
        isMaintainer={Boolean(me.data?.isMaintainer)}
        onReport={() => setReporting(true)}
        commands={commands}
        downloadHref={downloadHref}
      />
      {data.ranked === false && <UnrankedNotice reason={data.unrankedReason} />}
      {version?.status === "yanked" && (
        <YankedNotice version={version.version} reason={version.yankedReason} />
      )}
      {narrow ? (
        <Box
          data-testid="hub-entry-layout"
          sx={{ display: "flex", flexDirection: "column", gap: 3, mt: 1 }}
        >
          {intro && <Box data-testid="hub-entry-main">{intro}</Box>}
          {railFirst && (
            <Box
              component="aside"
              aria-label="About this entry"
              data-testid="hub-entry-rail"
              sx={STACK}
            >
              {railFirst}
            </Box>
          )}
          {tabs}
          <Box
            component="aside"
            aria-label="Provenance and publisher"
            data-testid="hub-entry-rail-rest"
            sx={STACK}
          >
            {railRest}
          </Box>
        </Box>
      ) : (
        <Box
          data-testid="hub-entry-layout"
          sx={{
            display: "grid",
            gap: 3,
            gridTemplateColumns: COLUMNS.lg,
            alignItems: "start",
            mt: 1,
          }}
        >
          <Box
            data-testid="hub-entry-main"
            sx={{
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {intro}
            {tabs}
          </Box>
          <Box
            component="aside"
            aria-label="About this entry"
            data-testid="hub-entry-rail"
            sx={STACK}
          >
            {railFirst}
            {railRest}
          </Box>
        </Box>
      )}
      <ReportDialog
        open={reporting}
        onClose={() => setReporting(false)}
        entryRef={data.ref}
        version={version?.version}
      />
    </HubFrame>
  );
}
