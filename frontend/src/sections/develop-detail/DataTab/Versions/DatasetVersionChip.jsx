import React, { useState } from "react";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import { alpha, Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  useAsOfVersion,
  useVersionChanges,
} from "src/api/develop/dataset-versions";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import VersionTagChip from "src/sections/common/Datasets/VersionTagChip";
import { KeyHintTitle } from "src/sections/common/ux/KeyHintTooltip";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { ItemsMenu } from "src/sections/common/ux/oneview/MenuItems";
import { fmtTimeRelative } from "src/sections/common/format/fmt";
import useVersionUrl from "./useVersionUrl";
import { versionChipMenuItems } from "./versionChipMenu";

/**
 * The dataset version in the header, after the dataset switcher
 * (PRODUCT-SPEC §3.2, §3.5; one view A1-10): `v6 latest golden-v4 ▾`, or
 * `Viewing v5 · golden-v3 ●` (amber) in the as-of view. A click opens the
 * version menu — `Version history  H` · `Compare two versions…` · the last
 * five versions — so version things live on the version (P4); `H` still
 * opens the panel.
 */
const DatasetVersionChipInner = () => {
  const { datasetId, toggleHistory, historyOpen, openCompare, viewVersion } =
    useVersionUrl();
  const { version, latest, versions, isAsOf } = useAsOfVersion(datasetId);
  const [anchor, setAnchor] = useState(null);
  const { data: since } = useVersionChanges(datasetId, version?.id, {
    against: "latest",
    enabled: isAsOf,
  });
  // The chip menu's two verbs by name in Cmd+K (P9): version history moved
  // here from a toolbar icon (A1-10), so it stays findable without the chip.
  useRegisterCommands(
    "dataset-version-chip",
    [
      {
        id: "versions.history",
        label: historyOpen ? "Hide version history" : "Version history",
        keywords: ["versions", "history", "changes", "restore"],
        shortcut: "history",
        icon: "mdi:history",
        run: () => toggleHistory(),
        when: () => Boolean(version),
      },
      {
        id: "versions.compare",
        label: "Compare two versions…",
        keywords: ["versions", "diff", "compare"],
        icon: "mdi:compare-horizontal",
        run: () => openCompare(),
        when: () => (versions?.length ?? 0) >= 2,
      },
    ],
    [historyOpen],
  );
  if (!version) return null;
  const shown = isAsOf ? version : latest ?? version;
  const total = latest?.number ?? shown.number;
  const by = shown.created_by?.name ?? "someone";
  const tag = shown.tags?.[0] ?? null;
  const stale =
    isAsOf && latest
      ? [
          since?.counts?.added ? `${since.counts.added} added` : null,
          since?.counts?.modified ? `${since.counts.modified} changed` : null,
          since?.counts?.deleted ? `${since.counts.deleted} deleted` : null,
        ].filter(Boolean)
      : [];
  // DL#33: what the chip is and what a click does.
  const tooltip = (
    <KeyHintTitle
      title={
        isAsOf
          ? `Viewing v${shown.number}, an older version · latest is v${total}${stale.length ? ` (${stale.join(", ")} since)` : ""}. Click for versions.`
          : `Version ${shown.number} of ${total} · saved ${fmtTimeRelative(
              shown.created_at,
            )} by ${by}. Click for versions.`
      }
      action="history"
    />
  );
  const items = versionChipMenuItems({
    versions,
    viewingNumber: shown.number,
    latestNumber: latest?.number ?? null,
    historyOpen,
    onHistory: toggleHistory,
    onCompare: openCompare,
    onView: (n) => viewVersion(n, { latestNumber: latest?.number }),
  });
  const open = Boolean(anchor);

  return (
    <>
      <CustomTooltip show size="small" placement="bottom" arrow title={tooltip}>
        <ButtonBase
          onClick={(e) => setAnchor(e.currentTarget)}
          aria-label={`Dataset version v${shown.number}`}
          aria-haspopup="menu"
          aria-expanded={open}
          data-testid="dataset-version-chip"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            height: 28,
            pl: 1,
            pr: 0.5,
            gap: 0.75,
            border: "1px solid",
            borderColor: isAsOf ? "amber.500" : "divider",
            borderRadius: 0.5,
            bgcolor: (t) =>
              isAsOf
                ? alpha(t.palette.amber[500], 0.12)
                : t.palette.background.paper,
            maxWidth: "100%",
            "&:hover": { bgcolor: "action.hover", borderColor: "border.hover" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ minWidth: 0 }}
          >
            {isAsOf && (
              <Typography typography="s2" color="text.secondary" noWrap>
                Viewing
              </Typography>
            )}
            <VersionNumber number={shown.number} />
            {shown.is_latest && (
              <Typography typography="s2" color="text.secondary" noWrap>
                latest
              </Typography>
            )}
            {tag && <VersionTagChip name={tag} size="small" />}
            {isAsOf && (
              <Box
                aria-label="Not the latest version"
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  bgcolor: "amber.500",
                  flexShrink: 0,
                }}
              />
            )}
          </Stack>
          <Iconify
            icon="mdi:chevron-down"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        </ButtonBase>
      </CustomTooltip>
      <ItemsMenu
        open={open}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        items={items}
        minWidth={260}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      />
    </>
  );
};

/**
 * L1 (the capability contract): not on the real backend, where it reads datasets/{id}/versions/, which dev does not have (a 404 on every dataset page).
 */
const DatasetVersionChip = () =>
  datasetCaps().saveVersion ? <DatasetVersionChipInner /> : null;

export default DatasetVersionChip;
