package server

// registerCapabilityEchoRoutes registers the test capability echo (plan V5).
// It is under /v1, so key-authenticated; the handler answers 404 unless the
// operator configured capabilities.echo with tools.enabled. Its routeStubs
// entry is the integrator's (SR6).
func (s *Server) registerCapabilityEchoRoutes(router *Router) {
	router.Handle("POST", "/v1/capabilities/echo", s.handlers.CapabilityEcho)
}
