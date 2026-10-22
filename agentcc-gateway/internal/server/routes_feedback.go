package server

// registerFeedbackRoutes registers outcome feedback: a caller's verdict on
// one of its generations, which outcome routing learns from.
func (s *Server) registerFeedbackRoutes(router *Router) {
	router.Handle("POST", "/v1/feedback", s.handleFeedback)
}
