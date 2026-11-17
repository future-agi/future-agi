/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Box,
  Chip,
  Collapse,
  IconButton,
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

const MCPPromptsTab = ({ mcpPrompts, isLoading }) => {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(null);

  const prompts = useMemo(() => mcpPrompts || [], [mcpPrompts]);

  const filtered = useMemo(() => {
    if (!search) return prompts;
    const q = search.toLowerCase();
    return prompts.filter(
      (p) =>
        (p.name || "").toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q),
    );
  }, [prompts, search]);

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
            <Skeleton width="25%" height={20} />
            <Skeleton width="35%" height={20} />
            <Skeleton width="15%" height={20} />
          </Stack>
        ))}
      </Section>
    );
  }

  return (
    <Box>
      <TextField
        size="small"
        placeholder="Search prompts..."
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
            {prompts.length === 0
              ? "No MCP prompts registered. Upstream servers may not expose prompts."
              : "No prompts match the current filter."}
          </Typography>
        </Section>
      ) : (
        <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" />
                  <TableCell>Name</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                    align="right"
                  >
                    Arguments
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((prompt) => {
                  const isExpanded = expanded === prompt.name;
                  const args = prompt.arguments || [];
                  return (
                    <React.Fragment key={prompt.name}>
                      <TableRow
                        hover
                        sx={{ cursor: args.length > 0 ? "pointer" : "default" }}
                        onClick={() =>
                          args.length > 0 &&
                          setExpanded(isExpanded ? null : prompt.name)
                        }
                      >
                        <TableCell padding="checkbox">
                          {args.length > 0 && (
                            <IconButton size="small">
                              <Iconify
                                icon={
                                  isExpanded
                                    ? "solar:alt-arrow-up-linear"
                                    : "solar:alt-arrow-down-linear"
                                }
                                width={20}
                              />
                            </IconButton>
                          )}
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            fontWeight={600}
                            sx={{ fontFamily: "monospace", fontSize: 13 }}
                          >
                            {prompt.name}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            color="text.secondary"
                            sx={{
                              maxWidth: 400,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                            title={prompt.description}
                          >
                            {prompt.description || "—"}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          {args.length}
                        </TableCell>
                      </TableRow>
                      {args.length > 0 && (
                        <TableRow>
                          <TableCell
                            colSpan={4}
                            sx={{
                              p: 0,
                              border: isExpanded ? undefined : "none",
                            }}
                          >
                            <Collapse in={isExpanded}>
                              <Box sx={{ p: 2, bgcolor: "action.hover" }}>
                                <Typography variant="subtitle2" gutterBottom>
                                  Arguments
                                </Typography>
                                <Stack spacing={1}>
                                  {args.map((arg) => (
                                    <Stack
                                      key={arg.name}
                                      direction="row"
                                      spacing={1}
                                      alignItems="center"
                                    >
                                      <Chip
                                        label={arg.name}
                                        size="small"
                                        color={
                                          arg.required ? "primary" : "default"
                                        }
                                        variant="outlined"
                                        sx={{
                                          fontFamily: "monospace",
                                        }}
                                      />
                                      {arg.required && (
                                        <Chip
                                          label="required"
                                          size="small"
                                          color="error"
                                          variant="outlined"
                                        />
                                      )}
                                      {arg.description && (
                                        <Typography
                                          variant="body2"
                                          color="text.secondary"
                                        >
                                          {arg.description}
                                        </Typography>
                                      )}
                                    </Stack>
                                  ))}
                                </Stack>
                              </Box>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Section>
      )}
    </Box>
  );
};

export default MCPPromptsTab;
