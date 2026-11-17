import React, { useContext, useId } from "react";
import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import {
  Alert, Box, Button, Drawer, Link, Stack, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Typography,
} from "@mui/material";
import { RouterLink } from "src/routes/components";
import { catalogBrowseState } from "../catalog/queryState";
import { copyText } from "../copyText";
import { modelPath } from "../modelLinks";
import { COMPARE_FIELDS, comparisonFacts } from "./comparisonFacts";
import { ModelCompareContext } from "./useModelCompare";

export default function ModelCompareDrawer({ data }) {
  const { ids, open, close, toggle } = useContext(ModelCompareContext);
  const location = useLocation();
  const titleId = useId();
  const { rows, markupPercent, catalog, status, providerAccessState } = data;
  const selected = ids.map((id) => {
    const row = rows?.find((item) => item.id === id);
    return { id, row, facts: comparisonFacts(row, markupPercent) };
  });
  const browseState = { gatewayModelBrowse: catalogBrowseState(location) };
  const share = () => copyText(new URL(
    `${location.pathname}${location.search}${location.hash}`, window.location.origin,
  ).href, "Comparison link copied");
  return (
    <Drawer anchor="right" open={open} onClose={close}
      PaperProps={{ role: "dialog", "aria-modal": true, "aria-labelledby": titleId,
        sx: { width: { xs: "100%", md: 1120 }, maxWidth: "100%", bgcolor: "background.paper" } }}>
      <Stack spacing={2} sx={{ p: { xs: 2, md: 3 }, minHeight: 0, height: "100%" }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
          <Typography id={titleId} component="h2" typography="h6">Compare models</Typography>
          <Button size="small" onClick={close}>Close comparison</Button>
        </Stack>
        <Typography typography="s2" color="text.secondary">
          Catalog facts, side by side. Prices are USD, one per model: what a request on Future AGI
          credits is charged where credits can buy the model, otherwise the provider&apos;s catalog
          quote, which your provider bills you directly on your own key (not proof of a configured
          route). No test requests are sent.
        </Typography>
        {ids.length < 2 && <Alert severity="info">Select at least two models to compare. Close this panel to add another.</Alert>}
        {(catalog.error || status.error) && (
          <Alert severity="warning">Catalog or model status may be stale. Close this panel and retry the failed read before relying on access or prices.</Alert>
        )}
        {providerAccessState !== "ready" && (
          <Alert severity="warning">Own-key access is not verified. Close this panel and retry provider access in the catalog.</Alert>
        )}
        {!rows ? (
          <Typography role="status" typography="s1">
            {catalog.error || status.error ? "Could not load comparison facts. Close this panel to retry the catalog." : "Loading comparison facts…"}
          </Typography>
        ) : (
          <TableContainer tabIndex={0} role="region" aria-label="Model comparison facts"
            sx={{ flex: 1, minHeight: 0, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1 }}>
            <Table size="small" stickyHeader aria-label="Model comparison"
              sx={{ minWidth: 200 + selected.length * 220, tableLayout: "fixed",
                "& th, & td": { verticalAlign: "top", borderColor: "divider", p: 1.5, overflowWrap: "anywhere" } }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: 200, bgcolor: "background.neutral" }}>Catalog fact</TableCell>
                  {selected.map(({ id, row }) => (
                    <TableCell key={id} scope="col" sx={{ bgcolor: "background.neutral" }}>
                      <Stack spacing={0.75}>
                        {row && modelPath(id) ? (
                          <Link component={RouterLink} href={modelPath(id)} state={browseState}
                            typography="s1" fontWeight="fontWeightMedium">{row.name}</Link>
                        ) : <Typography typography="s1">{id}</Typography>}
                        {row && <Typography typography="s2" color="text.secondary">{id}</Typography>}
                        <Stack direction="row" flexWrap="wrap" gap={0.75}>
                          {row && modelPath(id) && <>
                            {row.type === "chat" && <Button component={RouterLink} href={modelPath(id, "try")}
                              state={browseState} size="small" aria-label={`Try ${row.name}`}>Try</Button>}
                            <Button component={RouterLink} href={modelPath(id, "api")}
                              state={browseState} size="small" aria-label={`API ${row.name}`}>API</Button>
                          </>}
                          <Button size="small" aria-label={`Remove ${id}`} onClick={() => toggle(id)}>Remove</Button>
                        </Stack>
                      </Stack>
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {COMPARE_FIELDS.map(([key, label]) => (
                  <TableRow key={key}>
                    <TableCell component="th" scope="row">
                      <Typography typography="s2" fontWeight="fontWeightMedium">{label}</Typography>
                    </TableCell>
                    {selected.map(({ id, facts }) => (
                      <TableCell key={id}>
                        <Typography typography="s1" sx={{ whiteSpace: "pre-line", fontVariantNumeric: "tabular-nums" }}>
                          {facts[key].value}
                        </Typography>
                        {facts[key].reason && <Typography typography="s2" color="text.secondary" sx={{ mt: 0.5 }}>
                          {facts[key].reason}
                        </Typography>}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        <Box sx={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 1 }}>
          <Button variant="outlined" onClick={close}>Continue selecting</Button>
          <Button variant="contained" onClick={share}>Copy comparison link</Button>
        </Box>
      </Stack>
    </Drawer>
  );
}
ModelCompareDrawer.propTypes = { data: PropTypes.object.isRequired };
