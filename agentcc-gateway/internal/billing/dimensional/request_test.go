package dimensional

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDimensionalRequestBoundUsesContextNotTokenHeuristic(t *testing.T) {
	id := Identity{"synthetic/text", "mock", "snapshot"}
	card := Card{id, "synthetic-flat-v1", map[Unit]string{InputToken: "0.000002", OutputToken: "0.00001", InputImage: "0.000002"}}
	out := 10
	req := &models.ChatCompletionRequest{Model: id.Model, MaxTokens: &out, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	p, err := AdmitFlatInputChat(card, req, 1000)
	// 1000*2 + 10*10 + measured 0 images = 2100 micros, then 5% once.
	if err != nil || p.HoldMicros() != 2205 {
		t.Fatalf("plan=%+v error=%v", p, err)
	}
	if _, err := AdmitFlatInputChat(card, req, 0); err == nil {
		t.Fatal("unknown input bound accepted")
	}
	req.MaxTokens = nil
	if _, err := AdmitFlatInputChat(card, req, 1000); err == nil {
		t.Fatal("uncapped output accepted")
	}
	req.MaxTokens = &out
	req.Modalities = []string{"image"}
	if _, err := AdmitFlatInputChat(card, req, 1000); err == nil {
		t.Fatal("unqualified image output accepted")
	}
	req.Modalities = nil
	n := 2
	req.N = &n
	if _, err := AdmitFlatInputChat(card, req, 1000); err == nil {
		t.Fatal("uncapped candidates accepted")
	}
	req.N = nil
	req.Tools = []models.Tool{{Type: "web_search"}}
	if _, err := AdmitFlatInputChat(card, req, 1000); err == nil {
		t.Fatal("unbounded server work accepted")
	}
}
