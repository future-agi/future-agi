package mcp

import (
	"context"
	"errors"
	"testing"
)

func TestServedServerReadIsOrgScopedAndCountsReturnedEntries(t *testing.T) {
	s, _, ten, _ := servedFixture(t)
	ten.pools["org-b"].servers = append(ten.pools["org-b"].servers, PoolServer{ID: "foreign-only"})
	for _, id := range []string{"missing", "foreign-only"} {
		if _, err := s.AdminServedServerTools(context.Background(), "org-a", "vs_eng", "", id); !errors.Is(err, ErrUnknownServer) {
			t.Fatal("foreign or missing server did not produce not found")
		}
	}
	result, err := s.AdminServedServerTools(context.Background(), "org-a", "vs_eng", "", "github")
	if err != nil || len(result.Tools) != 2 {
		t.Fatal("server attribution changed runtime policy counts")
	}
	ten.snapshots["org-a"].MCP.VirtualServers["eng"].Mode = "search"
	result, err = s.AdminServedServerTools(context.Background(), "org-a", "vs_eng", "", "github")
	if err != nil || len(result.Tools) != 0 {
		t.Fatal("gateway search tools are not upstream tools")
	}
}
