import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import {
  getGatewayMetadataEntries,
  stringifyGatewayMetadata,
  stringifyGatewayMetadataValue,
} from "../utils/metadataDisplay";

export function GatewayMetadataJson({ metadata }) {
  return React.useMemo(() => stringifyGatewayMetadata(metadata), [metadata]);
}

GatewayMetadataJson.propTypes = {
  metadata: PropTypes.any,
};

export function GatewayMetadataTable({ metadata }) {
  const entries = React.useMemo(
    () => getGatewayMetadataEntries(metadata),
    [metadata],
  );

  if (entries.length === 0) {
    return (
      <Stack alignItems="center" py={4}>
        <Typography variant="body2" color="text.secondary">
          No metadata
        </Typography>
      </Stack>
    );
  }

  return (
    <Box p={2}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={{ fontWeight: 600 }}>Key</TableCell>
            <TableCell sx={{ fontWeight: 600 }}>Value</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {entries.map(([key, value]) => (
            <TableRow key={key}>
              <TableCell>{key}</TableCell>
              <TableCell sx={{ wordBreak: "break-all" }}>
                {stringifyGatewayMetadataValue(value)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

GatewayMetadataTable.propTypes = {
  metadata: PropTypes.object,
};
