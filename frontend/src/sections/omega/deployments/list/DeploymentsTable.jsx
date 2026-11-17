import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { Link as RouterLink, useNavigate } from "react-router-dom";

import { StatusChip } from "../components/StatusChip";
import { displayUrl, relativeTime } from "../lib/format";
import { deploymentPath } from "../lib/links";
import { hostOnlyNote, isHostOnly } from "../lib/reach";

const COLUMNS = ["Name", "Status", "Live revision", "URL", "Updated"];
const EMPTY = "—";
const mono = { fontFamily: "monospace", fontSize: 12 };
const stop = (event) => event.stopPropagation();

function liveText(deployment) {
  if (!deployment.liveRevision) return EMPTY;
  return `Revision ${deployment.liveRevision}`;
}

/**
 * The deployment's address. Only this host can reach it today (lib/reach.js),
 * and opened in a browser it is the deployment's own Studio page asking for
 * the access token, so it is text, never a link; a deployment with a network
 * address links to it.
 */
function UrlLink({ deployment }) {
  const { url } = deployment;
  if (!url) return <span>{EMPTY}</span>;
  if (isHostOnly(deployment)) {
    return (
      <Box
        component="span"
        title={hostOnlyNote(deployment)}
        sx={{ ...mono, color: "text.primary" }}
        data-testid="deployment-url"
        data-reach="deployment-host"
      >
        {displayUrl(url)}
      </Box>
    );
  }
  return (
    <Link
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      underline="hover"
      onClick={stop}
      onKeyDown={stop}
      sx={{ ...mono, color: "text.primary" }}
      data-testid="deployment-url"
    >
      {displayUrl(url)}
    </Link>
  );
}

UrlLink.propTypes = { deployment: PropTypes.object.isRequired };

function NameCell({ deployment, tabIndex }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Link
        component={RouterLink}
        to={deploymentPath(deployment.id)}
        tabIndex={tabIndex}
        onClick={stop}
        onKeyDown={stop}
        underline="hover"
        typography="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        sx={{ display: "block" }}
        noWrap
      >
        {deployment.name}
      </Link>
      <Typography typography="s3" color="text.secondary" sx={mono} noWrap>
        {deployment.id}
      </Typography>
    </Box>
  );
}

NameCell.propTypes = {
  deployment: PropTypes.object.isRequired,
  tabIndex: PropTypes.number,
};

function DeploymentCard({ deployment, onOpen }) {
  return (
    <Box
      role="link"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter") onOpen();
      }}
      data-testid="deployment-row"
      data-deployment-id={deployment.id}
      sx={{
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        "&:hover": { borderColor: "text.disabled" },
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          gap: 1,
          minWidth: 0,
          // The status stays readable while the adjacent name can shrink.
          "& > [data-status]": { flexShrink: 0 },
        }}
      >
        <NameCell deployment={deployment} tabIndex={-1} />
        <StatusChip
          status={deployment.status}
          hostOnly={isHostOnly(deployment)}
        />
      </Box>
      <Box
        sx={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <UrlLink deployment={deployment} />
      </Box>
      <Typography typography="s2" color="text.secondary">
        {liveText(deployment)} · updated {relativeTime(deployment.updatedAt)}
      </Typography>
    </Box>
  );
}

DeploymentCard.propTypes = {
  deployment: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
};

const headCell = {
  typography: "s2",
  fontWeight: 500,
  color: "text.secondary",
  bgcolor: "background.neutral",
  borderColor: "divider",
  py: 1,
};

export default function DeploymentsTable({ deployments }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
  const navigate = useNavigate();
  const open = (deployment) => navigate(deploymentPath(deployment.id));

  if (narrow) {
    return (
      <Box
        data-testid="deployments-list"
        sx={{ display: "flex", flexDirection: "column", gap: 1 }}
      >
        {deployments.map((deployment) => (
          <DeploymentCard
            key={deployment.id}
            deployment={deployment}
            onOpen={() => open(deployment)}
          />
        ))}
      </Box>
    );
  }

  return (
    <Box
      data-testid="deployments-list"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Table size="small" sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            {COLUMNS.map((column, index) => (
              <TableCell
                key={column}
                sx={{
                  ...headCell,
                  width: index === 0 ? "30%" : index === 3 ? "26%" : undefined,
                }}
              >
                {column}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {deployments.map((deployment) => (
            <TableRow
              key={deployment.id}
              hover
              data-testid="deployment-row"
              data-deployment-id={deployment.id}
              onClick={() => open(deployment)}
              sx={{
                cursor: "pointer",
                "& td": { borderColor: "divider", py: 1.25 },
              }}
            >
              <TableCell>
                <NameCell deployment={deployment} />
              </TableCell>
              <TableCell>
                <StatusChip
                  status={deployment.status}
                  hostOnly={isHostOnly(deployment)}
                />
              </TableCell>
              <TableCell
                sx={{
                  typography: "s1",
                  color: "text.primary",
                  whiteSpace: "nowrap",
                }}
              >
                {liveText(deployment)}
              </TableCell>
              <TableCell
                sx={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                <UrlLink deployment={deployment} />
              </TableCell>
              <TableCell
                sx={{
                  typography: "s1",
                  color: "text.secondary",
                  whiteSpace: "nowrap",
                }}
                title={deployment.updatedAt}
              >
                {relativeTime(deployment.updatedAt)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

DeploymentsTable.propTypes = {
  deployments: PropTypes.arrayOf(PropTypes.object).isRequired,
};
