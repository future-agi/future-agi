import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { shortDigest, timeAgo } from "../lib/format";
import { statusMeta } from "../lib/status";

import DateStamp from "./DateStamp";
import ToneChip from "./ToneChip";

// Semantic-version order: numbers compare as numbers (1.10.0 after 1.2.0),
// and a pre-release sorts before its release.
function compareVersions(a, b) {
  const [coreA, preA = ""] = String(a).split("-");
  const [coreB, preB = ""] = String(b).split("-");
  const partsA = coreA.split(".").map(Number);
  const partsB = coreB.split(".").map(Number);
  for (let index = 0; index < 3; index += 1) {
    const diff = (partsA[index] || 0) - (partsB[index] || 0);
    if (diff) return diff;
  }
  if (preA === preB) return 0;
  if (!preA) return 1;
  if (!preB) return -1;
  return preA.localeCompare(preB, "en", { numeric: true });
}

const byDate = (a, b) =>
  new Date(a.publishedAt).getTime() - new Date(b.publishedAt).getTime();
const byVersion = (a, b) => compareVersions(a.version, b.version);

// How long ago a version was published, in the Hub's one wording, or null
// when the date is missing or does not parse (the date itself reads "—").
function publishedAgo(iso) {
  if (!iso || Number.isNaN(new Date(iso).getTime())) return null;
  return timeAgo(iso);
}

const headCell = {
  typography: "s2",
  fontWeight: 500,
  color: "text.secondary",
  bgcolor: "background.neutral",
  borderColor: "divider",
  py: 1,
  whiteSpace: "nowrap",
};

function Status({ row }) {
  if (row.status === "yanked") {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
        <Box>
          <ToneChip tone="attention" label="Yanked" />
        </Box>
        <Typography typography="s2" color="text.secondary">
          {row.yankedReason || "No public reason given."}
        </Typography>
      </Box>
    );
  }
  const meta = statusMeta(row.status);
  return <ToneChip tone={meta.tone} label={meta.label} />;
}

Status.propTypes = { row: PropTypes.object.isRequired };

// The Versions tab (SPEC §7.2) as a dense table: version, published date with
// how long ago under it (hover for the exact time and its time zone), status
// (a yank shows its public reason), and the publisher's notes with a quiet
// line under them naming the bundle digest (whole on hover) and the rules
// version. It sorts by version or by date, newest first by default. Each
// version links to the page at that version; the latest drops the pin.
// Version, Published and Status take 340 px together and Notes takes the
// rest, so beside the side column with the app's sidebar open the notes keep
// about 200 px at 1200 and 280 px at 1280. Below 480 px (a phone) the
// wrapper scrolls sideways instead of squeezing the notes.
export default function VersionsTable({ versions, current, latest, hrefFor }) {
  const [sort, setSort] = useState({ by: "published", direction: "desc" });
  if (!versions?.length) {
    return (
      <Typography typography="s2" color="text.secondary">
        No versions to show.
      </Typography>
    );
  }
  const compare = sort.by === "version" ? byVersion : byDate;
  const rows = [...versions].sort((a, b) =>
    sort.direction === "asc" ? compare(a, b) : compare(b, a),
  );
  const sortBy = (by) =>
    setSort((was) => ({
      by,
      direction: was.by === by && was.direction === "desc" ? "asc" : "desc",
    }));
  const sortLabel = (by, label) => (
    <TableSortLabel
      active={sort.by === by}
      direction={sort.by === by ? sort.direction : "desc"}
      onClick={() => sortBy(by)}
    >
      {label}
    </TableSortLabel>
  );
  return (
    <Box
      data-testid="hub-versions"
      sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}
    >
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflowX: "auto",
        }}
      >
        <Table size="small" aria-label="Versions" sx={{ minWidth: 480 }}>
          <TableHead>
            <TableRow>
              <TableCell
                sortDirection={sort.by === "version" ? sort.direction : false}
                sx={{ ...headCell, width: 124 }}
              >
                {sortLabel("version", "Version")}
              </TableCell>
              <TableCell
                sortDirection={sort.by === "published" ? sort.direction : false}
                sx={{ ...headCell, width: 112 }}
              >
                {sortLabel("published", "Published")}
              </TableCell>
              <TableCell sx={{ ...headCell, width: 104 }}>Status</TableCell>
              <TableCell sx={headCell}>Notes</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => {
              const ago = publishedAgo(row.publishedAt);
              return (
                <TableRow
                  key={row.version}
                  data-testid="hub-version-row"
                  data-version={row.version}
                  selected={row.version === current && current !== latest}
                  aria-current={row.version === current ? "true" : undefined}
                  sx={{
                    "& td": {
                      borderColor: "divider",
                      py: 1,
                      verticalAlign: "top",
                    },
                    "&:last-child td": { borderBottom: 0 },
                  }}
                >
                  <TableCell>
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                        flexWrap: "wrap",
                      }}
                    >
                      <Link
                        component={RouterLink}
                        href={hrefFor(row.version)}
                        underline="hover"
                        sx={{
                          typography: "code_md",
                          fontWeight: 600,
                          color: "text.primary",
                        }}
                      >
                        {row.version}
                      </Link>
                      {row.version === latest && (
                        <ToneChip tone="success" label="Latest" />
                      )}
                      {row.version === current && current !== latest && (
                        <ToneChip tone="info" label="Showing" />
                      )}
                    </Box>
                  </TableCell>
                  <TableCell sx={{ typography: "s1", color: "text.primary" }}>
                    <DateStamp iso={row.publishedAt} />
                    {ago && (
                      <Typography
                        component="div"
                        typography="s2"
                        color="text.secondary"
                      >
                        {ago}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Status row={row} />
                  </TableCell>
                  <TableCell sx={{ typography: "s1", color: "text.primary" }}>
                    <Box sx={{ overflowWrap: "anywhere" }}>
                      {row.notes || (
                        <Box component="span" sx={{ color: "text.secondary" }}>
                          No notes.
                        </Box>
                      )}
                    </Box>
                    {row.digest || row.rulesVersion ? (
                      <Box
                        data-testid="hub-version-bundle"
                        sx={{
                          mt: 0.5,
                          display: "flex",
                          flexWrap: "wrap",
                          columnGap: 1.5,
                          typography: "code_sm",
                          color: "text.tertiary",
                        }}
                      >
                        {row.digest && (
                          <Box
                            component="span"
                            title={row.digest}
                            sx={{ whiteSpace: "nowrap" }}
                          >
                            {shortDigest(row.digest, 12)}
                          </Box>
                        )}
                        {row.rulesVersion ? (
                          <Box component="span" sx={{ whiteSpace: "nowrap" }}>
                            {`rules v${row.rulesVersion}`}
                          </Box>
                        ) : null}
                      </Box>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
      <Typography typography="s2" color="text.secondary">
        Each version stays available by its exact number. A yanked version shows
        its public reason here.
      </Typography>
    </Box>
  );
}

VersionsTable.propTypes = {
  versions: PropTypes.array,
  current: PropTypes.string,
  latest: PropTypes.string,
  hrefFor: PropTypes.func.isRequired,
};
