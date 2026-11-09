package mcp

import (
	"context"
	"errors"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

var ErrUnknownServer = errors.New("unknown MCP server")

// AdminServedServerTools narrows by attribution in the same resolved runtime
// context, never by reimplementing policy or resolving a second snapshot.
// Unknown and foreign server IDs produce the same not-found error.
func (s *Server) AdminServedServerTools(ctx context.Context, orgID, vs, keyID, serverID string) (*gatewayadmin.MCPAdminTools, error) {
	return s.adminServedTools(ctx, orgID, vs, keyID, serverID)
}
