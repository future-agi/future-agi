/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Box,
  InputAdornment,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Skeleton,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";

const MCPResourcesTab = ({ mcpResources, isLoading }) => {
  const [search, setSearch] = useState("");

  const resources = useMemo(() => mcpResources || [], [mcpResources]);

  const filtered = useMemo(() => {
    if (!search) return resources;
    const q = search.toLowerCase();
    return resources.filter(
      (r) =>
        (r.uri || "").toLowerCase().includes(q) ||
        (r.name || "").toLowerCase().includes(q) ||
        (r.description || "").toLowerCase().includes(q),
    );
  }, [resources, search]);

  if (isLoading) {
    return (
      <Section variant="panel">
        {[...Array(3)].map((_, i) => (
          <Stack
            key={i}
            direction="row"
            spacing={2}
            sx={{
              px: 2,
              py: 1.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Skeleton width="30%" height={20} />
            <Skeleton width="20%" height={20} />
            <Skeleton width="30%" height={20} />
          </Stack>
        ))}
      </Section>
    );
  }

  return (
    <Box>
      <TextField
        size="small"
        placeholder="Search resources..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        sx={{ mb: 2, minWidth: 280 }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify icon="solar:magnifier-linear" width={16} />
            </InputAdornment>
          ),
        }}
      />

      {filtered.length === 0 ? (
        <Section variant="panel">
          <Typography
            variant="body2"
            color="text.secondary"
            align="center"
            sx={{ py: 3 }}
          >
            {resources.length === 0
              ? "No MCP resources registered. Upstream servers may not expose resources."
              : "No resources match the current filter."}
          </Typography>
        </Section>
      ) : (
        <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>URI</TableCell>
                  <TableCell>Name</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Description</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((res) => (
                  <TableRow key={res.uri} hover>
                    <TableCell>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        sx={{ fontFamily: "monospace", fontSize: 13 }}
                      >
                        {res.uri}
                      </Typography>
                    </TableCell>
                    <TableCell>{res.name || "—"}</TableCell>
                    <TableCell>
                      {res.mimeType ? (
                        <Typography variant="body2" color="text.secondary">
                          {res.mimeType}
                        </Typography>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{
                          maxWidth: 300,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={res.description}
                      >
                        {res.description || "—"}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Section>
      )}
    </Box>
  );
};

export default MCPResourcesTab;
