import React, { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { HOST_API } from "src/config-global";
import { RouterLink } from "src/routes/components";

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
import DisclosurePanel from "./components/DisclosurePanel";
import DiscordLink from "./components/DiscordLink";
import EntryFiles from "./components/EntryFiles";
import EntryHeader from "./components/EntryHeader";
import EvidencePanel from "./components/EvidencePanel";
import HubFrame from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import { HubError, HubSkeletonPage, HubState } from "./components/HubStates";
import InstallsStat from "./components/InstallsStat";
import KeysPanel from "./components/KeysPanel";
import ModerateMenu from "./components/ModerateMenu";
import { UnrankedNotice, YankedNotice } from "./components/Notices";
import PublisherCard from "./components/PublisherCard";
import ReadmeView, { isRemoteSrc } from "./components/ReadmeView";
import RemixBlock from "./components/RemixBlock";
import ReportDialog from "./components/ReportDialog";
import RestoreButton from "./components/RestoreButton";
import Tombstone from "./components/Tombstone";
import UseThisMenu from "./components/UseThisMenu";
import VersionsTable from "./components/VersionsTable";
import { TAB_ROW_PROPS, TAB_ROW_SCROLL_SX } from "./components/tabRow";

const TABS = ["card", "files", "versions"];

// One entry (SPEC §7.2): header, Card / Files / Versions, and the right rail.
// There is no on-page conversation; talk goes to Discord.
export default function HubEntryPage() {
  const { publisher, name, tab: rawTab } = useParams();
  const tab = TABS.includes(rawTab) ? rawTab : "card";
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
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
  // On a phone the README comes first, under one Use this block; the rest of the
  // rail follows it (HUB-23). One UseThisMenu is rendered, in the right place.
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
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
        back={{ href: hubPaths.browse, label: "Hub" }}
        documentTitle={`${publisher}/${name}`}
      >
        <HubSkeletonPage />
      </HubFrame>
    );
  }
  if (entry.error?.tombstone) {
    return (
      <HubFrame
        back={{ href: hubPaths.browse, label: "Hub" }}
        documentTitle={`${publisher}/${name}`}
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
      >
        <HubError error={entry.error} onRetry={() => entry.refetch()} />
      </HubFrame>
    );
  }

  const unavailableVersion = [404, 410].includes(pinned.error?.status);
  const signedIn = Boolean(me.data?.user);
  const manifest = version?.manifest || {};
  const useThis = version ? (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {/* The public bundle works without a published CLI or native handoff. */}
      <Button
        component="a"
        href={`${hubApiUrl(hubApi.versionBundle(publisher, name, version.version))}?source=web`}
        target="_blank"
        rel="noopener noreferrer"
        variant="outlined"
        startIcon={<HubIcon name="download" size={16} />}
        data-testid="hub-download-bundle"
      >
        Download (.tar.gz)
      </Button>
      <UseThisMenu
        entryRef={data.ref}
        version={version.version}
        capabilities={version.capabilities}
      />
    </Box>
  ) : null;
  const setTab = (next) =>
    navigate(
      `${hubPaths.entry(publisher, name, next)}${window.location.search}`,
    );
  const setVersion = (next) => {
    const params = new URLSearchParams(search);
    if (next === latest) params.delete("version");
    else params.set("version", next);
    setSearch(params, { replace: true });
  };
  const resolveImage = (src) =>
    version && !isRemoteSrc(src)
      ? `${HOST_API || ""}${hubApi.versionFile(publisher, name, version.version, src.replace(/^\.\//, ""))}`
      : null;

  return (
    <HubFrame
      back={{ href: hubPaths.browse, label: "Hub" }}
      documentTitle={data.ref}
      testId="hub-entry"
    >
      <EntryHeader
        entry={data}
        version={version}
        versions={versions.data}
        onVersion={setVersion}
        signedIn={signedIn}
        moderate={
          me.data?.isMaintainer ? (
            <ModerateMenu entry={data} version={version?.version} />
          ) : null
        }
      />
      {data.ranked === false && <UnrankedNotice reason={data.unrankedReason} />}
      {version?.status === "yanked" && (
        <YankedNotice version={version.version} reason={version.yankedReason} />
      )}
      <Box
        sx={{
          display: "grid",
          gap: 3,
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            md: "minmax(0, 1fr) 320px",
          },
          alignItems: "start",
        }}
      >
        <Box
          sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}
        >
          {narrow && useThis}
          <Tabs
            value={tab}
            onChange={(_, next) => setTab(next)}
            {...TAB_ROW_PROPS}
            sx={{
              borderBottom: "1px solid",
              borderColor: "divider",
              minHeight: 40,
              "& .MuiTab-root": { minHeight: 40 },
              ...TAB_ROW_SCROLL_SX,
            }}
          >
            <Tab value="card" label="Card" data-testid="hub-tab-card" />
            <Tab
              value="files"
              label={`Files${version?.files ? ` (${version.files.length})` : ""}`}
              data-testid="hub-tab-files"
            />
            <Tab
              value="versions"
              label={`Versions${data.versionCount ? ` (${data.versionCount})` : ""}`}
              data-testid="hub-tab-versions"
            />
          </Tabs>
          {tab === "versions" ? (
            versions.isLoading ? (
              <HubSkeletonPage />
            ) : versions.isError ? (
              <HubError
                compact
                error={versions.error}
                onRetry={() => versions.refetch()}
              />
            ) : (
              <VersionsTable versions={versions.data} current={wanted} />
            )
          ) : wanted !== latest && pinned.isError ? (
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
                      : undefined
                }
                error={pinned.error}
                onRetry={
                  unavailableVersion ? undefined : () => pinned.refetch()
                }
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
          ) : !version ? (
            <HubSkeletonPage />
          ) : tab === "files" ? (
            <EntryFiles
              key={version.version}
              publisher={publisher}
              name={name}
              version={version.version}
              files={version.files}
            />
          ) : (
            <ReadmeView markdown={version.readme} resolveImage={resolveImage} />
          )}
        </Box>
        <Box
          sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}
        >
          {!narrow && useThis}
          {/* InstallsStat frames itself, and is not shown where the install path is off (HIDE-UNFINISHED-FA). */}
          <InstallsStat
            installs30d={data.installs30d}
            opens30d={data.opens30d}
            series={data.installsDaily30}
          />
          <KeysPanel keys={manifest.keys} />
          <DisclosurePanel disclosure={version?.disclosure} />
          <EvidencePanel version={version} tier={data.publisher?.tier} />
          <RemixBlock
            publisher={publisher}
            name={name}
            remixOf={data.remixOf}
            remixCount={data.remixCount}
            version={version?.version}
          />
          <PublisherCard publisher={data.publisher} />
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
            <DiscordLink fullWidth />
            <Button
              size="small"
              color="inherit"
              onClick={() => setReporting(true)}
              startIcon={<HubIcon name="flag" size={15} />}
              sx={{ justifyContent: "flex-start", color: "text.secondary" }}
              data-testid="hub-report-button"
            >
              Report
            </Button>
          </Box>
        </Box>
      </Box>
      <ReportDialog
        open={reporting}
        onClose={() => setReporting(false)}
        entryRef={data.ref}
        version={version?.version}
      />
    </HubFrame>
  );
}
