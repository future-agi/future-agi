package server

// registerModelsRoutes registers the developer model list and lookup.
func (s *Server) registerModelsRoutes(router *Router) {
	router.Handle("GET", "/v1/models", s.handlers.ListModels)
	router.Handle("GET", "/v1/models/{model}", s.handlers.GetModel)
}
