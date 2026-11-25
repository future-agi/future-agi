package gemini

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func errorsAs(err error, target **models.APIError) bool { return errors.As(err, target) }

const testKey = "AIzaSyD-ThisIsTheShapeOfARealGoogleKey_123456"

// seenRequest records how a request reached the upstream.
type seenRequest struct {
	rawQuery string
	fullURL  string
	header   string
}

// callWith runs one call against a recording server and reports what the
// upstream saw.
func callWith(t *testing.T, call func(p *Provider) error) seenRequest {
	t.Helper()
	var seen seenRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen.rawQuery = r.URL.RawQuery
		seen.fullURL = r.URL.String()
		seen.header = r.Header.Get("x-goog-api-key")
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}],"embeddings":[{"values":[0.1]}]}`))
	}))
	defer server.Close()

	p, err := New("g", config.ProviderConfig{BaseURL: server.URL, APIKey: testKey})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := call(p); err != nil {
		t.Fatalf("call: %v", err)
	}
	return seen
}

// The key must travel in a header. In the URL it ends up in every *url.Error,
// and from there in the caller's 502 body and the request log.
func TestCredential_TravelsInAHeaderNotTheURL(t *testing.T) {
	chat := &models.ChatCompletionRequest{
		Model:    "gemini-3-flash-preview",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	}

	tests := []struct {
		name string
		call func(p *Provider) error
	}{
		{"chat completion", func(p *Provider) error {
			_, err := p.ChatCompletion(context.Background(), chat)
			return err
		}},
		{"streaming chat completion", func(p *Provider) error {
			chunks, errs := p.StreamChatCompletion(context.Background(), chat)
			for {
				select {
				case _, ok := <-chunks:
					if !ok {
						return nil
					}
				case err := <-errs:
					return err
				}
			}
		}},
		{"embedding", func(p *Provider) error {
			_, err := p.CreateEmbedding(context.Background(), &models.EmbeddingRequest{
				Model: "text-embedding-004", Input: json.RawMessage(`"hi"`),
			})
			return err
		}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			seen := callWith(t, tc.call)

			if strings.Contains(seen.fullURL, testKey) {
				t.Errorf("the key was in the URL: %s", seen.fullURL)
			}
			if strings.Contains(seen.rawQuery, "key=") {
				t.Errorf("a key query parameter was sent: %s", seen.rawQuery)
			}
			if seen.header != testKey {
				t.Errorf("x-goog-api-key = %q, want the configured key", seen.header)
			}
		})
	}
}

// The reported failure: when the call cannot complete, the error reaches the
// caller and the request log. It must carry no credential.
func TestCredential_IsAbsentFromAFailedCallsError(t *testing.T) {
	// A server that is closed at once, so the call fails at the network level
	// and Go builds a *url.Error carrying the whole URL.
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := server.URL
	server.Close()

	p, err := New("g", config.ProviderConfig{BaseURL: url, APIKey: testKey})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	_, callErr := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "gemini-3-flash-preview",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})
	if callErr == nil {
		t.Fatal("the call succeeded against a closed server")
	}

	var apiErr *models.APIError
	if !errorsAs(callErr, &apiErr) {
		t.Fatalf("error %v is not an *models.APIError", callErr)
	}
	if strings.Contains(apiErr.Message, testKey) {
		t.Fatalf("the key reached the error message:\n%s", apiErr.Message)
	}
	if strings.Contains(apiErr.Error(), testKey) {
		t.Fatalf("the key reached Error():\n%s", apiErr.Error())
	}
}

// Go carries a custom header through every redirect, stripping only the ones
// it knows. Moving the key into x-goog-api-key therefore made it follow the
// upstream wherever it pointed.
func TestCredential_IsNotForwardedAcrossARedirect(t *testing.T) {
	var elsewhereSawKey atomic.Bool
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-goog-api-key") != "" {
			elsewhereSawKey.Store(true)
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}`))
	}))
	defer elsewhere.Close()

	redirector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, elsewhere.URL+r.URL.Path, http.StatusTemporaryRedirect)
	}))
	defer redirector.Close()

	p, err := New("g", config.ProviderConfig{BaseURL: redirector.URL, APIKey: testKey})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	// The call may succeed or fail; what matters is where the key went.
	p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "gemini-3-flash-preview",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	if elsewhereSawKey.Load() {
		t.Error("the credential was forwarded to another origin across a redirect")
	}
}

// A redirect from https to plaintext is refused rather than followed, so the
// credential is never put on the wire in the clear.
func TestCredential_HTTPSDowngradeRedirectIsRefused(t *testing.T) {
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{}`))
	}))
	defer plain.Close()

	tlsUpstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, plain.URL+r.URL.Path, http.StatusTemporaryRedirect)
	}))
	defer tlsUpstream.Close()

	p, err := New("g", config.ProviderConfig{BaseURL: tlsUpstream.URL, APIKey: testKey, SkipTLS: true})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	_, callErr := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "gemini-3-flash-preview",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})
	if callErr == nil {
		t.Fatal("the downgrade redirect was followed")
	}
	var apiErr *models.APIError
	if errorsAs(callErr, &apiErr) && strings.Contains(apiErr.Message, testKey) {
		t.Errorf("the refusal message carries the credential: %s", apiErr.Message)
	}
}

// Go rebuilds each redirected request from the original, so a header dropped
// on one hop comes back on the next. Judging a hop against the one before it
// therefore let A -> B -> B restore the credential at B: the second hop looked
// like it stayed put. Every hop is judged against the configured origin.
func TestCredential_IsNotRestoredOnALaterHop(t *testing.T) {
	var mu sync.Mutex
	var sawKeyOnHop []int
	hop := 0

	var elsewhereURL string
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hop++
		n := hop
		if r.Header.Get("x-goog-api-key") != "" {
			sawKeyOnHop = append(sawKeyOnHop, n)
		}
		mu.Unlock()

		if n == 1 {
			// A second hop inside the same foreign origin.
			http.Redirect(w, r, elsewhereURL+r.URL.Path+"?hop=2", http.StatusTemporaryRedirect)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}`))
	}))
	defer elsewhere.Close()
	elsewhereURL = elsewhere.URL

	configured := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, elsewhereURL+r.URL.Path, http.StatusTemporaryRedirect)
	}))
	defer configured.Close()

	p, err := New("g", config.ProviderConfig{BaseURL: configured.URL, APIKey: testKey})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{
		Model:    "gemini-3-flash-preview",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})

	mu.Lock()
	defer mu.Unlock()
	if len(sawKeyOnHop) > 0 {
		t.Errorf("the credential reached a foreign origin on hop(s) %v of %d", sawKeyOnHop, hop)
	}
	if hop < 2 {
		t.Fatalf("only %d hops ran; the multi-hop path was not exercised", hop)
	}
}
