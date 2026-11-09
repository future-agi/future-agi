package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate/catalogparts"
)

// initConnectors loads the connector catalog (CONNECTORS-PLAN 2.6 item 1):
// the bundle the gateway embeds (dist/catalog.json), or the operator's
// connectors.catalog_dir in its place. A catalog that does not load stops the
// gateway: every server added from it would otherwise be refused. The MCP
// pool (tenancy, IR-CN7-3) and the org config admin PUT and bulk load (CN7
// review P2) place a catalog_ref server's credential by the parts its
// provider declares; the handlers get it whether or not MCP is on, so a
// config is refused at the push exactly when the pool would refuse it.
func initConnectors(d *wiringDeps) error {
	dir := ""
	if d.cfg != nil {
		dir = d.cfg.Connectors.CatalogDir
	}
	cat, err := connectors.Load(dir)
	if err != nil {
		return err
	}
	d.connectorCatalog = catalogparts.FromManifest(cat.Records)
	slog.Info("connector catalog loaded", "source", cat.Source, "apps", len(cat.Records.Apps))
	d.afterServer(func() error {
		if d.server != nil {
			if h := d.server.OrgConfigHandlers(); h != nil {
				h.SetConnectorCatalog(d.connectorCatalog)
			}
		}
		return nil
	})
	return nil
}
