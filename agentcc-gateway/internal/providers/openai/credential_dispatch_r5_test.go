package openai

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type r5Transport func(*http.Request) (*http.Response, error)

func (f r5Transport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestR5InclusiveDispatchAndInheritedHeader(t *testing.T) {
	sent := "inclusive-dispatched-opaque-abcdefghijklmnopqrstuvwxyz"
	held := "configured-but-not-sent-value"
	header := "inherited-header-opaque-abcdefghijklmnopqrstuvwxyz"
	for _, mode := range []string{"http", "dial", "body", "stream"} {
		t.Run(mode, func(t *testing.T) {
			cfg := config.ProviderConfig{BaseURL: "http://127.0.0.1:1", APIKey: held, Headers: map[string]string{"X-Extra-Secret": header}}
			p, err := New("inclusive-instance", cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			p.httpClient.Transport = r5Transport(func(r *http.Request) (*http.Response, error) {
				if r.Header.Get("Authorization") != "Bearer "+sent || r.Header.Get("X-Extra-Secret") != header {
					t.Fatal("wrong outbound credential witness")
				}
				text := "before " + sent + " and " + header + " after " + held
				switch mode {
				case "dial":
					return nil, errors.New(text)
				case "body":
					return &http.Response{StatusCode: 200, Body: r5FailedBody{text}, Header: http.Header{}, Request: r}, nil
				case "stream":
					b, _ := json.Marshal(map[string]any{"error": map[string]string{"message": text}})
					return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader("data: " + string(b) + "\n\n")), Header: http.Header{}, Request: r}, nil
				default:
					b, _ := json.Marshal(map[string]any{"error": map[string]string{"message": text, "code": sent, "type": header}})
					return &http.Response{StatusCode: 400, Body: io.NopCloser(strings.NewReader(string(b))), Header: http.Header{}, Request: r}, nil
				}
			})
			ctx := models.WithInclusiveCredential(context.Background(), p.ID(), sent)
			req := &models.ChatCompletionRequest{Model: "unit"}
			if mode == "stream" {
				ch, er := p.StreamChatCompletion(ctx, req)
				for range ch {
				}
				err = <-er
			} else {
				_, err = p.ChatCompletion(ctx, req)
			}
			if err == nil {
				t.Fatal("missing error")
			}
			text := r5Text(err)
			if strings.Contains(text, sent) || strings.Contains(text, header) {
				t.Fatal("actual dispatch credential leaked")
			}
			if !strings.Contains(text, "before") || !strings.Contains(text, " after "+held) {
				t.Fatal("changed unrelated diagnostic text or scrubbed unsent config key")
			}
		})
	}
}

type r5FailedBody struct{ text string }

func (b r5FailedBody) Read([]byte) (int, error) { return 0, errors.New(b.text) }
func (b r5FailedBody) Close() error             { return nil }

func TestR5SpeechHTTPOutboundCanary(t *testing.T) {
	key := "speech-outbound-opaque-abcdefghijklmnopqrstuvwxyz"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+key {
			t.Error("wrong outbound speech key")
		}
		w.WriteHeader(400)
		json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "before " + key + " after", "code": key, "type": key}})
	}))
	defer upstream.Close()
	p, err := New("speech", config.ProviderConfig{BaseURL: upstream.URL, APIKey: key})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	_, _, err = p.CreateSpeech(context.Background(), &models.SpeechRequest{Model: "unit", Input: "hello"})
	if err == nil || strings.Contains(r5Text(err), key) || !strings.Contains(r5Text(err), "before [REDACTED] after") {
		t.Fatal("speech HTTP error source boundary failed")
	}
}
