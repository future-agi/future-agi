/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Box,
  Card,
  CardContent,
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
import Iconify from "src/components/iconify";
import { useMCPPrompts } from "../hooks/useMCPConfig";
import { emptyListText } from "../utils";

const PromptsTab = ({ gatewayId }) => {
  const { data: mcpPrompts, isLoading, isError } = useMCPPrompts(gatewayId);
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
      <Card>
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
      </Card>
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
              <Iconify icon="mdi:magnify" width={20} />
            </InputAdornment>
          ),
        }}
      />

      {filtered.length === 0 ? (
        <Card>
          <CardContent>
            <Typography
              variant="body2"
              color="text.secondary"
              align="center"
              sx={{ py: 3 }}
            >
              {emptyListText(
                isError,
                prompts,
                "prompts",
                "No MCP prompts registered. Upstream servers may not expose prompts.",
                "No prompts match the current filter.",
              )}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" />
                  <TableCell>Name</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell align="right">Arguments</TableCell>
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
                                    ? "mdi:chevron-up"
                                    : "mdi:chevron-down"
                                }
                                width={18}
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
                        <TableCell align="right">{args.length}</TableCell>
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
                                          fontSize: 12,
                                        }}
                                      />
                                      {arg.required && (
                                        <Chip
                                          label="required"
                                          size="small"
                                          color="error"
                                          variant="outlined"
                                          sx={{ fontSize: 10, height: 18 }}
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
        </Card>
      )}
    </Box>
  );
};

export default PromptsTab;
