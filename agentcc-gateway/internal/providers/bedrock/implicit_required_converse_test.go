package bedrock

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestConverseRequiredImplicitAllowedTool(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		var wire converseRequest
		if err := json.NewDecoder(r.Body).Decode(&wire); err != nil {
			t.Error(err)
		} else if tc := wire.ToolConfig; tc == nil {
			t.Error("Converse request omitted toolConfig for required implicit function")
		} else {
			if len(tc.Tools) != 1 || tc.Tools[0].ToolSpec.Name != "lookup" {
				t.Errorf("forwarded tools = %+v, want only lookup", tc.Tools)
			}
			if !reflect.DeepEqual(tc.ToolChoice, map[string]any{"any": map[string]any{}}) {
				t.Errorf("Converse toolChoice = %+v, want any", tc.ToolChoice)
			}
		}
		_, _ = io.WriteString(w, `{"output":{"message":{"role":"assistant","content":[{"text":"ok"}]}},"stopReason":"end_turn"}`)
	}))
	defer upstream.Close()
	p, err := New("synthetic", config.ProviderConfig{BaseURL: upstream.URL, AWSRegion: "us-east-1", AWSAccessKeyID: "synthetic", AWSSecretAccessKey: "synthetic"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()

	const declarations = `[{"type":"function","function":{"name":"other","parameters":{"type":"object"}}},{"function":{"name":"lookup","parameters":{"type":"object"}}}]`
	var req models.ChatCompletionRequest
	req.Model = "us.test-profile"
	req.Messages = []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}
	req.ToolChoice = json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"required","tools":[{"type":"function","name":"lookup"}]}}`)
	var want []models.Tool
	for _, dst := range []*[]models.Tool{&req.Tools, &want} {
		if err := json.Unmarshal([]byte(declarations), dst); err != nil {
			t.Fatal(err)
		}
	}
	original := req.Tools
	if _, err := p.ChatCompletion(context.Background(), &req); err != nil {
		t.Fatal(err)
	}
	if n := calls.Load(); n != 1 {
		t.Errorf("Converse upstream received %d requests, want 1", n)
	}
	if !reflect.DeepEqual(original, want) || !reflect.DeepEqual(req.Tools, want) {
		t.Errorf("caller tools changed: original slice = %+v, request tools = %+v, want %+v", original, req.Tools, want)
	}
}
