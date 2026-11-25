package realtime

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/gorilla/websocket"
)

type modelCheck struct{ seen *[]string }

func (g modelCheck) Name() string            { return "model-check" }
func (g modelCheck) Stage() guardrails.Stage { return guardrails.StagePre }
func (g modelCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	*g.seen = append(*g.seen, in.Request.Model)
	return &guardrails.CheckResult{Pass: true}
}

func TestSpeechGuardrailCartesiaChecksRequestModels(t *testing.T) {
	seen := []string{}
	g := modelCheck{&seen}
	registry := map[string]guardrails.Guardrail{g.Name(): g}
	engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: g.Name(), Stage: "pre", Action: "block"}}}, registry)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	policy, apiErr := guardrails.NewPlugin(engine, registry, nil, nil, nil).BindRealtime(rc, guardrails.RealtimeTextBoth)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	inspector := NewSpeechGuardrails(context.Background(), policy, "cartesia")
	for _, event := range []string{
		`{"model_id":"sonic-2","context_id":"a","transcript":"one"}`,
		`{"model_id":"sonic-2","context_id":"b","transcript":"two","continue":true}`,
		`{"model_id":"sonic-3","context_id":"b","transcript":"three"}`,
		`{"context_id":"c","transcript":"four"}`,
	} {
		if _, err := inspector.Client(websocket.TextMessage, []byte(event)); err != nil {
			t.Fatal(err)
		}
	}
	want := []string{"cartesia/sonic-2", "cartesia/sonic-2", "cartesia/sonic-3", "cartesia/sonic-3"}
	if !slices.Equal(seen, want) {
		t.Fatalf("checked models=%v want=%v", seen, want)
	}
}
