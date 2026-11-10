package bedrock

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/testutil/policystream"
)

func TestConverseToolPolicies(t *testing.T) {
	for _, policy := range []string{"none", "allowed", "required", "named", "parallel"} {
		for _, stream := range []bool{false, true} {
			name := policy
			if stream {
				name += "/stream"
			}
			t.Run(name, func(t *testing.T) {
				var calls atomic.Int32
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
					data, _ := io.ReadAll(r.Body)
					wire := string(data)
					t.Logf("converse upstream=%s", wire)
					switch policy {
					case "none":
						if strings.Contains(wire, "toolConfig") {
							t.Error("disabled tools still offered")
						}
					case "allowed":
						if strings.Contains(wire, "restricted_tool") || !strings.Contains(wire, "permitted_tool") {
							t.Error("allowed subset not applied")
						}
					case "required":
						if !strings.Contains(wire, `"toolChoice":{"any":{}}`) {
							t.Error("required tool choice lost")
						}
					case "named":
						if !strings.Contains(wire, `"toolChoice":{"tool":{"name":"permitted_tool"}}`) {
							t.Error("named tool choice lost")
						}
					}
					if stream {
						policystream.Reply(w, "converse", policy == "required" || policy == "named")
						return
					}
					io.WriteString(w, `{"output":{"message":{"role":"assistant","content":[{"text":"ok"}]}},"stopReason":"end_turn","usage":{"inputTokens":1,"outputTokens":1}}`)
				}))
				defer upstream.Close()
				p, err := New("synthetic", config.ProviderConfig{BaseURL: upstream.URL, AWSRegion: "us-east-1", AWSAccessKeyID: "synthetic", AWSSecretAccessKey: "synthetic"})
				if err != nil {
					t.Fatal(err)
				}
				defer p.Close()
				body := `{"model":"us.test-profile","messages":[{"role":"user","content":"synthetic"}],"tools":[{"type":"function","function":{"name":"permitted_tool","parameters":{"type":"object"}}},{"type":"function","function":{"name":"restricted_tool","parameters":{"type":"object"}}}]`
				switch policy {
				case "none":
					body += `,"tool_choice":"none"`
				case "allowed", "required":
					mode := "auto"
					if policy == "required" {
						mode = "required"
					}
					body += `,"tool_choice":{"type":"allowed_tools","allowed_tools":{"mode":"` + mode + `","tools":[{"type":"function","name":"permitted_tool"}]}}`
				case "named":
					body += `,"tool_choice":{"type":"function","function":{"name":"permitted_tool"}}`
				case "parallel":
					body += `,"parallel_tool_calls":false`
				}
				body += "}"
				var req models.ChatCompletionRequest
				if err = json.Unmarshal([]byte(body), &req); err != nil {
					t.Fatal(err)
				}
				var received []models.StreamChunk
				if stream {
					received, err = policystream.Collect(p.StreamChatCompletion(context.Background(), &req))
				} else {
					_, err = p.ChatCompletion(context.Background(), &req)
				}
				if policy == "parallel" {
					if apiErr, ok := err.(*models.APIError); !ok || apiErr.Status != 400 {
						t.Errorf("error=%v, want 400", err)
					}
					if calls.Load() != 0 {
						t.Errorf("unsupported policy dispatched %d calls", calls.Load())
					}
				} else {
					if err != nil {
						t.Fatal(err)
					}
					if stream {
						policystream.CheckSuccess(t, received, policy == "required" || policy == "named")
					}
					if calls.Load() != 1 {
						t.Errorf("calls=%d, want 1", calls.Load())
					}
				}
			})
		}
	}
}
