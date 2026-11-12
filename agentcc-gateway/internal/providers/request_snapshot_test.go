package providers

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Hold the admission slot across StreamChatCompletion's return, change the
// caller's routing fields, then admit its goroutine. The wire must still name
// the model supplied at call time. No sleep or live provider is involved.
func TestG1ProviderRequestSnapshot(t *testing.T) {
	for _, adapter := range []string{"openai", "anthropic", "azure", "bedrock", "bedrock-converse", "cohere", "gemini"} {
		t.Run(adapter, func(t *testing.T) {
			first, next := "g1-first", "g1-fallback"
			apiFormat := adapter
			if adapter == "bedrock-converse" {
				apiFormat, first, next = "bedrock", "us.g1-first", "us.g1-fallback"
			}
			wire := make(chan string, 1)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var body struct {
					Model string `json:"model"`
				}
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				model := body.Model
				switch apiFormat {
				case "azure":
					model = strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/openai/deployments/"), "/chat/completions")
				case "bedrock":
					model = strings.Split(strings.TrimPrefix(r.URL.Path, "/model/"), "/")[0]
				case "gemini":
					_, model, _ = strings.Cut(r.URL.Path, "/models/")
					model, _, _ = strings.Cut(model, ":")
				}
				wire <- model
				w.WriteHeader(http.StatusBadRequest)
				fmt.Fprint(w, `{"error":{"message":"synthetic terminal response"}}`)
			}))
			defer upstream.Close()
			p, err := createProvider("g1-"+adapter, config.ProviderConfig{
				APIFormat: apiFormat, BaseURL: upstream.URL, APIKey: "synthetic",
				AWSAccessKeyID: "synthetic", AWSSecretAccessKey: "synthetic", AWSRegion: "us-east-1",
				MaxConcurrent: 1, QueueTimeout: 5 * time.Second, DefaultTimeout: 5 * time.Second,
				DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
					host, _, err := net.SplitHostPort(address)
					ip := net.ParseIP(host)
					if err != nil || ip == nil || !ip.IsLoopback() {
						return nil, fmt.Errorf("non-loopback provider connection refused")
					}
					return (&net.Dialer{}).DialContext(ctx, network, address)
				},
			})
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			slots := p.(SlotHolder).Admission()
			if err := slots.Acquire(ctx); err != nil {
				t.Fatal(err)
			}
			req := &models.ChatCompletionRequest{Model: first, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}}
			chunks, errs := p.StreamChatCompletion(ctx, req)
			req.Model = next
			slots.Release()
			for chunks != nil || errs != nil {
				select {
				case _, ok := <-chunks:
					if !ok {
						chunks = nil
					}
				case _, ok := <-errs:
					if !ok {
						errs = nil
					}
				case <-ctx.Done():
					t.Fatal("adapter did not terminate")
				}
			}
			select {
			case got := <-wire:
				if got != first {
					t.Errorf("sent %q after caller moved to %q; admitted model was %q", got, next, first)
				}
			default:
				t.Fatal("no loopback upstream request")
			}
		})
	}
}
