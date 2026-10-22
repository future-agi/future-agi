package main

import (
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/catalog/sources"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// initCatalog configures the managed providers' catalogs (history,
// snapshots, blocks, fees), makes prices catalog-first with the model
// database behind it, and once the server runs serves the catalogs and
// keeps them current: model lists every managed.catalog_refresh, and the
// newest fixed-price models' endpoints every catalog.endpoints_refresh.
//
// Each managed provider's source is resolved against its catalog_source
// first: a generic OpenAI-compatible list gets its parser, and a price card
// is read (a card that does not load, or whose fee disagrees with the
// provider's upstream_fee_bps, fails startup) and seeded, so its models
// route and price before the server starts.
func initCatalog(d *wiringDeps) error {
	resolved, err := sources.Resolve(d.cfg, d.catalogSources)
	if err != nil {
		return err
	}
	d.catalogSources = resolved
	if err := d.catalogStore.Configure(catalog.OptionsFromConfig(d.cfg, d.catalogSources)); err != nil {
		return err
	}
	catalog.SeedStatic(d.catalogStore, d.catalogSources)
	d.prices = d.catalogStore.PriceLookup(d.prices)
	d.afterServer(func() error {
		if len(d.catalogSources) == 0 {
			return nil
		}
		d.server.EnableManaged(server.ManagedSettings{
			Catalog:        d.catalogStore,
			Ledger:         d.orgLedger,
			MarkupPercent:  d.cfg.Managed.MarkupPercent,
			RequireCredits: d.cfg.Managed.RequireCredits,
		})
		// Configure may have restored a fresher persisted catalog than the
		// one the model database was built from, and SeedStatic added the
		// price cards.
		d.server.RefreshModelDB()
		refresher := catalog.NewRefresher(d.ctx, d.catalogStore, d.catalogSources, d.server.RefreshModelDB)
		go refresher.Run(d.cfg.Managed.CatalogRefresh)
		go refresher.RunEndpoints()
		return nil
	})
	return nil
}
