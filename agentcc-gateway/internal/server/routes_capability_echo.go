package server

// registerCapabilityEchoRoutes registers the test capability echo.
// It is under /v1, so key-authenticated; the handler answers 404 unless the
// operator configured capabilities.echo with tools.enabled.
func (s *Server) registerCapabilityEchoRoutes(router *Router) {
	router.Handle("POST", "/v1/capabilities/echo", s.handlers.CapabilityEcho)
}
