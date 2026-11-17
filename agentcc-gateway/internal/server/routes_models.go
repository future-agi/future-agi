package server

// registerModelsRoutes registers the developer model list and lookup.
func (s *Server) registerModelsRoutes(router *Router) {
	router.Handle("GET", "/v1/models", s.handlers.ListModels)
	router.Handle("GET", "/v1/models/{model}", s.handlers.GetModel)
	// Deliberately outside /v1: no KeyAuth or LicenseAuth exception.
	router.Handle("GET", "/public/model-prices/v1.json", s.modelPricesHandler())
}
