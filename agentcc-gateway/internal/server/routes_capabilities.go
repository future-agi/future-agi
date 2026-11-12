package server

// registerCapabilitiesRoutes registers the capability registry: the matrix
// for the caller's key, and the admin view of every provider.
func (s *Server) registerCapabilitiesRoutes(router *Router) {
	c := newCapabilityRoutes(s)
	router.Handle("GET", "/v1/capabilities", c.handleKey)
	router.Handle("GET", "/-/capabilities", c.handleAdmin)
}
