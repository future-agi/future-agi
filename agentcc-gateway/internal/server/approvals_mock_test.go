package server

import (
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

// Reuse the repository's real MCP mock and the scenario's synthetic fixture.
// Its received log is an independent dispatch oracle, not gateway evidence.
func w5MockUpstream(t *testing.T) (*mcpmock.Mock, string) {
	t.Helper()
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	fixtures, err := mcpmock.LoadFixtures("../../scripts/scenarios/fixtures/m26")
	if err != nil {
		t.Fatal(err)
	}
	mock, err := mcpmock.New(mcpmock.Config{Era: era}, fixtures)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewUnstartedServer(mock.Handler())
	mock.SetPublicURL("http://" + srv.Listener.Addr().String())
	srv.Start()
	t.Cleanup(srv.Close)
	return mock, srv.URL + "/mcp"
}
