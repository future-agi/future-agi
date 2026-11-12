import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Breadcrumbs from "@mui/material/Breadcrumbs";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import ListSubheader from "@mui/material/ListSubheader";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { KINDS, KIND_LABEL, hubPaths } from "../constants";
import { formatBytes, formatDate, splitRef } from "../lib/format";

import CopyCommand, { CopyButton, copyText } from "./CopyCommand";
import DateStamp from "./DateStamp";
import EntryCover, { PublisherAvatar } from "./EntryCover";
import HubIcon from "./HubIcon";
import LikeButton from "./LikeButton";
import { useModeration } from "./ModerateMenu";
import TierBadge from "./TierBadge";
import ToneChip from "./ToneChip";

// The width of the entry page's side column; the title block's action box
// lines up with it.
const SIDE = 340;

const LEVEL = {
  starter: "Starter",
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
};

const levelLabel = (value) =>
  value
    ? LEVEL[value] || `${value.charAt(0).toUpperCase()}${value.slice(1)}`
    : null;

const keylessLine = (keyless) =>
  keyless === "scripted"
    ? "Runs without a key. A scripted model stands in for a live one."
    : "Runs without a key.";

// The title block's main action, where a package page puts its install box.
// Once a customer can install a CLI with `omega hub` (the page passes the
// pinned commands): Try it now with the whole run command, then Add it to
// your project, then the download. Before that, the download of the
// approved files is the action. No command is cut: each wraps whole.
function TryBox({ commands, version, downloadHref, bundleBytes, keyless }) {
  const titleId = useId();
  const size = bundleBytes ? formatBytes(bundleBytes) : null;
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid="hub-try"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        minWidth: 0,
      }}
    >
      {commands ? (
        <>
          <Typography
            id={titleId}
            component="h2"
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            data-testid="hub-cli-run-caption"
          >
            Try it now
          </Typography>
          <CopyCommand
            text={commands.run}
            label="Copy the run command"
            testId="hub-cli-run"
          />
          <Typography typography="s2" color="text.secondary">
            {keylessLine(keyless)}
          </Typography>
          <Typography
            component="h3"
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            data-testid="hub-cli-add-caption"
            sx={{ mt: 1 }}
          >
            Add it to your project
          </Typography>
          <CopyCommand
            text={commands.add}
            label="Copy the add command"
            testId="hub-cli-add"
          />
          <Box
            sx={{
              mt: 0.75,
              pt: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
            }}
          >
            <Link
              href={downloadHref}
              target="_blank"
              rel="noopener noreferrer"
              underline="hover"
              typography="s1"
              data-testid="hub-download-bundle"
            >
              Download (.tar.gz)
            </Link>
            {size && (
              <Typography typography="s2" color="text.secondary">
                {size}
              </Typography>
            )}
          </Box>
        </>
      ) : (
        <>
          <Typography
            id={titleId}
            component="h2"
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            Get the files
          </Typography>
          <Typography typography="s1" color="text.secondary">
            {`The approved files of version ${version} in one archive${size ? `, ${size}` : ""}.`}
          </Typography>
          <Button
            component="a"
            href={downloadHref}
            target="_blank"
            rel="noopener noreferrer"
            variant="contained"
            color="primary"
            data-testid="hub-download-bundle"
            sx={{ alignSelf: "flex-start" }}
          >
            Download (.tar.gz)
          </Button>
        </>
      )}
    </Box>
  );
}

TryBox.propTypes = {
  commands: PropTypes.shape({ run: PropTypes.string, add: PropTypes.string }),
  version: PropTypes.string.isRequired,
  downloadHref: PropTypes.string.isRequired,
  bundleBytes: PropTypes.number,
  keyless: PropTypes.string,
};

// More: what a visitor does now and then (copy the pinned reference,
// download the bundle, report a problem), then, for a maintainer only, a
// labelled Maintainer group with every moderation action. Escape closes it
// and focus goes back to the More button.
function MoreMenu({ entry, version, downloadHref, isMaintainer, onReport }) {
  const [anchor, setAnchor] = useState(null);
  const [copied, setCopied] = useState(false);
  const buttonId = useId();
  const menuId = useId();
  const moderation = useModeration({ entry, version });
  const reference = `hub:${entry.ref}${version ? `@${version}` : ""}`;
  const close = () => setAnchor(null);
  const copyReference = async () => {
    close();
    if (await copyText(reference)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };
  const items = [
    <MenuItem key="copy" onClick={copyReference}>
      Copy reference
    </MenuItem>,
    downloadHref ? (
      <MenuItem
        key="download"
        component="a"
        href={downloadHref}
        target="_blank"
        rel="noopener noreferrer"
        onClick={close}
      >
        Download .tar.gz
      </MenuItem>
    ) : null,
    <MenuItem
      key="report"
      data-testid="hub-report-button"
      onClick={() => {
        close();
        onReport();
      }}
    >
      Report a problem
    </MenuItem>,
    ...(isMaintainer
      ? [
          <Divider key="maintainer-divider" />,
          <ListSubheader
            key="maintainer"
            sx={{ lineHeight: "28px", typography: "s3", fontWeight: 600 }}
          >
            Maintainer
          </ListSubheader>,
          ...moderation.items.map((item) => (
            <MenuItem
              key={item.key}
              onClick={() => {
                close();
                moderation.choose(item.key);
              }}
            >
              {item.label}
            </MenuItem>
          )),
        ]
      : []),
  ].filter(Boolean);
  return (
    <>
      <Button
        id={buttonId}
        size="small"
        color="inherit"
        onClick={(event) => setAnchor(event.currentTarget)}
        aria-haspopup="menu"
        aria-controls={anchor ? menuId : undefined}
        aria-expanded={anchor ? "true" : undefined}
        endIcon={<HubIcon name="chevronDown" size={14} />}
        data-testid="hub-more-button"
        sx={{
          minWidth: 0,
          px: 1,
          color: "text.secondary",
          "&:hover": { color: "text.primary" },
        }}
      >
        More
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        MenuListProps={{ id: menuId, "aria-labelledby": buttonId, dense: true }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        {items}
      </Menu>
      <Box
        component="span"
        aria-live="polite"
        sx={{ typography: "s2", color: "text.secondary" }}
      >
        {copied ? "Reference copied" : ""}
      </Box>
      {isMaintainer && moderation.dialogs}
    </>
  );
}

MoreMenu.propTypes = {
  entry: PropTypes.object.isRequired,
  version: PropTypes.string,
  downloadHref: PropTypes.string,
  isMaintainer: PropTypes.bool,
  onReport: PropTypes.func.isRequired,
};

// The entry's title block (SPEC §7.2), the first thing on the page and the
// one place to act from: a breadcrumb, the cover and the title as the page's
// heading, the tagline, a byline (publisher, tier, when it was checked and
// approved and how long ago that was, and the commit only when the version
// records one), one quiet metadata line (kind, level, licence, version),
// the tags, then a quiet Like and More. At its right, in line with the side
// column, the main action: the commands to run and add it, or the download.
export default function EntryHeader({
  entry,
  version,
  signedIn,
  isMaintainer = false,
  onReport,
  commands,
  downloadHref,
}) {
  const publisher = entry.publisher || {};
  const manifest = version?.manifest || {};
  const pinnedRef = version?.version
    ? `hub:${entry.ref}@${version.version}`
    : `hub:${entry.ref}`;
  const derived = entry.remixOf ? splitRef(entry.remixOf) : null;
  const kindPlural = KINDS.find((kind) => kind.value === entry.kind)?.label;
  const commit = version?.sourceCommit
    ? version.sourceCommit.slice(0, 7)
    : null;
  const facts = [
    KIND_LABEL[entry.kind] || entry.kind,
    levelLabel(manifest.difficulty),
    manifest.license,
    version?.version && `Version ${version.version}`,
    manifest.requires?.chromium && "Needs Chromium",
  ].filter(Boolean);
  const tags = entry.tags || [];
  const action =
    version && downloadHref ? (
      <TryBox
        commands={commands}
        version={version.version}
        downloadHref={downloadHref}
        bundleBytes={version.bundleBytes}
        keyless={manifest.models?.keyless}
      />
    ) : null;
  return (
    <Box
      component="header"
      data-testid="hub-entry-header"
      sx={{
        display: "grid",
        gap: 3,
        gridTemplateColumns: {
          xs: "minmax(0, 1fr)",
          lg: action ? `minmax(0, 1fr) ${SIDE}px` : "minmax(0, 1fr)",
        },
        alignItems: "start",
        pb: 3,
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}
      >
        <Breadcrumbs aria-label="Breadcrumb" sx={{ typography: "s1" }}>
          <Link
            component={RouterLink}
            href={hubPaths.browse}
            underline="hover"
            color="text.secondary"
          >
            Hub
          </Link>
          {kindPlural && (
            <Link
              component={RouterLink}
              href={`${hubPaths.browse}?kind=${encodeURIComponent(entry.kind)}`}
              underline="hover"
              color="text.secondary"
            >
              {kindPlural}
            </Link>
          )}
          <Typography
            typography="code_md"
            color="text.primary"
            aria-current="page"
            data-testid="hub-entry-name"
          >
            {entry.name}
          </Typography>
        </Breadcrumbs>
        <Box
          sx={{
            display: "flex",
            gap: 2,
            alignItems: "flex-start",
            minWidth: 0,
          }}
        >
          <EntryCover cover={entry.cover} size={48} />
          <Box
            sx={{
              minWidth: 0,
              flex: 1,
              display: "flex",
              flexDirection: "column",
              gap: 0.75,
            }}
          >
            <Typography
              component="h1"
              typography="l3"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
              data-testid="hub-entry-title"
              sx={{ overflowWrap: "anywhere" }}
            >
              {entry.title || entry.name}
            </Typography>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.25,
                minWidth: 0,
              }}
            >
              <Typography
                component="span"
                typography="code_md"
                color="text.secondary"
                data-testid="hub-entry-ref"
                sx={{ overflowWrap: "anywhere" }}
              >
                {entry.ref}
              </Typography>
              <CopyButton
                text={pinnedRef}
                label={`Copy reference ${entry.ref}`}
                tooltip="Copy reference"
                size={13}
              />
            </Box>
            {entry.tagline && (
              <Typography
                typography="s1_2"
                color="text.secondary"
                data-testid="hub-entry-tagline"
                sx={{ maxWidth: 760 }}
              >
                {entry.tagline}
              </Typography>
            )}
            <Box
              data-testid="hub-entry-byline"
              sx={{
                display: "flex",
                alignItems: "center",
                flexWrap: "wrap",
                columnGap: 1.25,
                rowGap: 0.5,
                mt: 0.5,
                typography: "s1",
                color: "text.secondary",
                minWidth: 0,
              }}
            >
              <Box
                component="span"
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.75 }}
              >
                <PublisherAvatar publisher={publisher} size={20} />
                <Link
                  component={RouterLink}
                  href={hubPaths.publisher(publisher.handle)}
                  underline="hover"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  {publisher.displayName || publisher.handle}
                </Link>
              </Box>
              <TierBadge tier={publisher.tier} quiet />
              {version?.approvedAt && (
                <Box component="span">
                  {"Checked and approved "}
                  <DateStamp iso={version.approvedAt} withAgo />
                  {commit && (
                    <Box component="span" sx={{ whiteSpace: "nowrap" }}>
                      {" · from commit "}
                      <Box
                        component="code"
                        title={version.sourceCommit}
                        sx={{ typography: "code_md" }}
                      >
                        {commit}
                      </Box>
                    </Box>
                  )}
                </Box>
              )}
            </Box>
            {facts.length > 0 && (
              <Typography
                typography="s1"
                color="text.tertiary"
                data-testid="hub-entry-facts"
              >
                {facts.join(" · ")}
              </Typography>
            )}
            {derived && (
              <Typography
                typography="s1"
                color="text.secondary"
                data-testid="hub-derived-from"
              >
                {"Derived from "}
                <Link
                  component={RouterLink}
                  href={hubPaths.entry(derived.publisher, derived.name)}
                  underline="hover"
                >
                  {`${derived.publisher}/${derived.name}`}
                </Link>
              </Typography>
            )}
            {(tags.length > 0 || entry.pick) && (
              <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
                {entry.pick && (
                  <ToneChip
                    tone="attention"
                    label={`Maintainer pick · week of ${formatDate(entry.pick.week)}`}
                  />
                )}
                {tags.map((tag) => (
                  <Chip
                    key={tag}
                    label={tag}
                    size="small"
                    variant="outlined"
                    clickable
                    component={RouterLink}
                    href={`${hubPaths.browse}?tag=${encodeURIComponent(tag)}`}
                    aria-label={`Entries tagged ${tag}`}
                  />
                ))}
              </Box>
            )}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                flexWrap: "wrap",
                ml: -1,
              }}
            >
              <Box data-testid="hub-entry-like">
                <LikeButton
                  publisher={publisher.handle}
                  name={entry.name}
                  likes={entry.likes}
                  likedByMe={entry.likedByMe}
                  signedIn={signedIn}
                />
              </Box>
              <MoreMenu
                entry={entry}
                version={version?.version}
                downloadHref={downloadHref}
                isMaintainer={isMaintainer}
                onReport={onReport}
              />
            </Box>
          </Box>
        </Box>
      </Box>
      {action}
    </Box>
  );
}

EntryHeader.propTypes = {
  entry: PropTypes.object.isRequired,
  version: PropTypes.object,
  signedIn: PropTypes.bool,
  isMaintainer: PropTypes.bool,
  onReport: PropTypes.func.isRequired,
  commands: PropTypes.shape({ run: PropTypes.string, add: PropTypes.string }),
  downloadHref: PropTypes.string,
};
