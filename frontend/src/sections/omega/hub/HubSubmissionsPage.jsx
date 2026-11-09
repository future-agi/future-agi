import React from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CardActionArea from "@mui/material/CardActionArea";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { hubPaths } from "./constants";
import { useMySubmissions } from "./api/queries";
import { timeAgo } from "./lib/format";
import HubFrame from "./components/HubFrame";
import HubIcon from "./components/HubIcon";
import HubNav from "./components/HubNav";
import { HubEmpty, HubError, HubSkeletonCards } from "./components/HubStates";
import { StatusChip } from "./components/StatusTimeline";

function refOf(row) {
  const name = row.name || row.hints?.name;
  const version = row.version || row.hints?.version;
  if (!name) return `${row.publisher}/…`;
  return `${row.publisher}/${name}${version ? `@${version}` : ""}`;
}

// My submissions (SPEC §7.1) with live status: the list refreshes while any
// row is being checked or reviewed.
export default function HubSubmissionsPage() {
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const submissions = useMySubmissions();
  const rows = submissions.data || [];
  return (
    <HubFrame
      title="My submissions"
      documentTitle="My submissions"
      description="Everything you or your publishers uploaded, with where it is in checks and review."
      testId="hub-submissions"
    >
      <HubNav />
      {submissions.isLoading ? (
        <HubSkeletonCards count={3} />
      ) : submissions.isError ? (
        <HubError
          error={submissions.error}
          onRetry={() => submissions.refetch()}
        />
      ) : rows.length === 0 ? (
        <HubEmpty
          title="Nothing submitted yet"
          description={`${cliReleased ? "Publish from your terminal or upload a folder" : "Upload a folder"}. You will follow it here through checks and review.`}
          action={
            <Button
              component={RouterLink}
              href={hubPaths.publish}
              variant="contained"
              size="small"
            >
              Publish
            </Button>
          }
        />
      ) : (
        <Box
          component="ul"
          sx={{
            m: 0,
            p: 0,
            listStyle: "none",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        >
          {rows.map((row, index) => (
            <Box
              component="li"
              key={row.id}
              sx={{
                borderTop: index ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              <CardActionArea
                component={RouterLink}
                href={hubPaths.submission(row.id)}
                data-testid="hub-submission-row"
                data-status={row.status}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.5,
                  px: 2,
                  py: 1.5,
                  flexWrap: { xs: "wrap", sm: "nowrap" },
                }}
              >
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography
                    typography="s1"
                    fontWeight={600}
                    sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
                  >
                    {refOf(row)}
                  </Typography>
                  <Typography typography="s2" color="text.secondary">
                    {row.statusText}
                  </Typography>
                </Box>
                <StatusChip status={row.status} />
                <Typography
                  typography="s3"
                  color="text.tertiary"
                  sx={{ width: 90, textAlign: "right" }}
                >
                  {timeAgo(row.updatedAt || row.createdAt)}
                </Typography>
                <Box
                  sx={{
                    color: "text.disabled",
                    display: { xs: "none", sm: "flex" },
                  }}
                >
                  <HubIcon name="chevronRight" size={16} />
                </Box>
              </CardActionArea>
            </Box>
          ))}
        </Box>
      )}
    </HubFrame>
  );
}
