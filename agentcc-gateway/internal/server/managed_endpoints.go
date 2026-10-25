package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// managedEndpoint reports whether managed models serve rc's endpoint. Only
// chat completions do: chat rewrites the upstream model ID and bills the
// upstream-reported cost, which every managed request needs.
func managedEndpoint(rc *models.RequestContext) bool {
	return rc.EndpointType == "" || rc.EndpointType == "chat"
}
