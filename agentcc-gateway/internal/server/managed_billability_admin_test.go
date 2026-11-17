package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

func TestManagedBillabilityAdminAvailability(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	installBillabilityModels(billabilityModels(t))(t, g)
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, _ := http.NewRequest(http.MethodGet, ts.URL+"/-/catalog", nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var data struct {
		Models []catalogModelView `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("catalogue status=%d decode=%v", resp.StatusCode, err)
	}
	byID := map[string]catalogModelView{}
	for _, row := range data.Models {
		byID[row.ID] = row
	}
	for _, id := range refusedBillabilityIDs {
		row, listed := byID[id]
		if !listed {
			continue
		} // The two unlisted-id guards are not admin rows.
		if row.State != catalog.StateBlocked || row.StateReason == "" || row.InputPerMTok != nil || row.BilledInputPerMTok != nil {
			t.Errorf("unavailable view for %s: %+v", id, row)
		}
	}
	for _, id := range []string{"acme/token", "acme/request", "acme/listed:free", "openrouter/free", "acme/mixed-endpoints", "acme/endpoint:free"} {
		row, ok := byID[id]
		if !ok || row.State == catalog.StateBlocked || row.BilledInputPerMTok == nil {
			t.Errorf("supported view for %s: %+v", id, row)
		}
	}
}
