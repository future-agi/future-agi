import React, { useState } from "react";
import {
  Box,
  Button,
  Card,
  CardContent,
  Collapse,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  REQUEST_CHECK_NOT_SERVED,
  REQUEST_CHECK_ROUTES,
} from "../constants/guardrailRequestRoutes";

// Which requests a Before LLM check reads, route by route, as the gateway
// enforces it (constants/guardrailRequestRoutes.js, from
// api_contracts/gateway/guardrail-request-routes.json). Collapsed by default:
// the summary line says what holds on every route.
const RequestRoutesCard = () => {
  const [open, setOpen] = useState(false);

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
        >
          <Box>
            <Typography variant="subtitle2">
              Requests Before LLM checks read
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Before LLM checks read the request on{" "}
              {REQUEST_CHECK_ROUTES.length} routes. A request a check blocks
              never reaches the model, and a mask or redaction reaches it or the
              request is refused. Where a check cannot read part of a request
              (an audio file, a document, token ids), a check that would block
              or mask it refuses the request.
            </Typography>
          </Box>
          <Button
            size="small"
            onClick={() => setOpen((v) => !v)}
            endIcon={
              <Iconify
                icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
                width={18}
              />
            }
            sx={{ flexShrink: 0 }}
          >
            {open ? "Hide routes" : "Show routes"}
          </Button>
        </Stack>
        <Collapse in={open} unmountOnExit>
          <TableContainer sx={{ mt: 2 }}>
            <Table size="small" aria-label="Routes Before LLM checks read">
              <TableHead>
                <TableRow>
                  <TableCell>Route</TableCell>
                  <TableCell>What the checks read</TableCell>
                  <TableCell>
                    Not read: refused by a check that blocks or masks
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {REQUEST_CHECK_ROUTES.map((r) => (
                  <TableRow key={r.route}>
                    <TableCell>
                      <Typography
                        variant="body2"
                        sx={{ fontFamily: "monospace", whiteSpace: "nowrap" }}
                      >
                        {r.route}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{r.reads}</Typography>
                      {r.note && (
                        <Typography variant="caption" color="text.secondary">
                          {r.note}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {r.unread || "—"}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography
            variant="caption"
            color="text.secondary"
            component="p"
            mt={1}
          >
            This gateway has no route for {REQUEST_CHECK_NOT_SERVED.join(", ")};
            Realtime answers 501.
          </Typography>
        </Collapse>
      </CardContent>
    </Card>
  );
};

export default RequestRoutesCard;
