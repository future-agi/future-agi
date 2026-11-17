package server

// registerCreditsAdminRoutes registers the org managed-credit account the
// control plane reads and pushes granted totals to (enabled by
// EnableManaged). The legacy POST and PUT on the account are retired (410).
func (s *Server) registerCreditsAdminRoutes(router *Router) {
	router.Handle("GET", "/-/orgs/{org_id}/credits", s.managedRoute(s.handleOrgCredits))
	router.Handle("PUT", "/-/orgs/{org_id}/credits/granted", s.managedRoute(s.handleOrgCreditsGranted))
	router.Handle("POST", "/-/orgs/{org_id}/credits", s.managedRoute(s.handleRetiredOrgCredits))
	router.Handle("PUT", "/-/orgs/{org_id}/credits", s.managedRoute(s.handleRetiredOrgCredits))
}
