package main

import (
	"encoding/json"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
)

type routingStub struct{ snapshot auth.RoutingSnapshot }

func (r routingStub) RoutingSnapshot() auth.RoutingSnapshot { return r.snapshot }

func TestHealthBodyReportsSimulationRouting(t *testing.T) {
	router := routingStub{auth.RoutingSnapshot{
		Dropped:        map[string]int64{"no_agent": 3, "column_missing": 0},
		ColumnsPresent: true,
	}}
	raw, err := json.Marshal(healthBody("ok", chwriter.Stats{}, router))
	if err != nil {
		t.Fatal(err)
	}
	var body struct {
		Status  string         `json:"status"`
		Routing map[string]any `json:"simulation_routing"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body.Status != "ok" || body.Routing["no_agent"] != float64(3) || body.Routing["column_missing"] != float64(0) || body.Routing["columns_present"] != true {
		t.Fatalf("body = %s", raw)
	}
	// Without a router the body is what it was.
	raw, err = json.Marshal(healthBody("unhealthy", chwriter.Stats{}, nil))
	if err != nil {
		t.Fatal(err)
	}
	var plain map[string]any
	if err := json.Unmarshal(raw, &plain); err != nil {
		t.Fatal(err)
	}
	if _, present := plain["simulation_routing"]; present || plain["status"] != "unhealthy" {
		t.Fatalf("body = %s", raw)
	}
}
