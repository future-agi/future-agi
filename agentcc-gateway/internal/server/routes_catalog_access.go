package server

// registerCatalogAccessRoutes registers the per-org catalog access view: which
// canonical models an org reaches through managed access, its own keys, or
// both (admin token).
func (s *Server) registerCatalogAccessRoutes(router *Router) {
	router.Handle("GET", "/-/orgs/{org_id}/catalog/access", s.handleCatalogAccess)
}
