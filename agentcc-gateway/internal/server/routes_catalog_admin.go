package server

// registerCatalogAdminRoutes registers the managed catalog the console reads:
// the model list, endpoints, stats, history and kept snapshots, the admin
// refresh, and runtime blocks.
func (s *Server) registerCatalogAdminRoutes(router *Router) {
	router.Handle("GET", "/-/catalog", s.managedRoute(s.handleCatalog))
	router.Handle("GET", "/-/catalog/endpoints", s.managedRoute(s.handleCatalogEndpoints))
	router.Handle("GET", "/-/catalog/stats", s.managedRoute(s.handleCatalogStats))
	router.Handle("GET", "/-/catalog/history", s.managedRoute(s.handleCatalogHistory))
	router.Handle("GET", "/-/catalog/snapshots/{id}", s.managedRoute(s.handleCatalogSnapshot))
	router.Handle("POST", "/-/catalog/refresh", s.managedRoute(s.handleCatalogRefresh))
	router.Handle("PUT", "/-/catalog/blocks", s.managedRoute(s.handleCatalogBlocks))
}
