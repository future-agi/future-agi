//go:build cataloguepreflighttest

package server

import "net/http"

// CataloguePreflightHandler exposes the already-built real router to the
// contained test launcher. It registers/replaces no route and bypasses no
// middleware or admin authentication. Absent from every release build.
func (s *Server) CataloguePreflightHandler() http.Handler { return s.httpServer.Handler }
