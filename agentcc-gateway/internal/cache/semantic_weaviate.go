package cache

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// WeaviateBackend provides semantic caching via Weaviate vector database.
type WeaviateBackend struct {
	client    *http.Client
	baseURL   string
	className string
	apiKey    string
	threshold float64
	dims      int
}

// NewWeaviateBackend creates a Weaviate-backed semantic cache.
func NewWeaviateBackend(baseURL, className, apiKey string, threshold float64, dims int, timeout time.Duration) (*WeaviateBackend, error) {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	if threshold <= 0 || threshold > 1 {
		threshold = 0.85
	}
	if dims <= 0 {
		dims = 256
	}
	if className == "" {
		className = "AgentccSemanticCache"
	}

	w := &WeaviateBackend{
		client:    &http.Client{Timeout: timeout},
		baseURL:   baseURL,
		className: className,
		apiKey:    apiKey,
		threshold: threshold,
		dims:      dims,
	}

	// Ensure schema class exists.
	w.ensureClass()
	return w, nil
}

func (w *WeaviateBackend) Search(vector []float32, model string) *SearchResult {
	nowUnix := time.Now().Unix()

	// GraphQL nearVector query with where filter.
	gql := map[string]interface{}{
		"query": `{
			Get {
				` + w.className + `(
					nearVector: {vector: ` + vectorJSON(vector) + `, certainty: ` + floatStr(w.threshold) + `}
					where: {
						operator: And
						operands: [
							{path: ["model_name"], operator: Equal, valueText: "` + model + `"},
							{path: ["expires_at"], operator: GreaterThan, valueInt: ` + intStr(nowUnix) + `}
						]
					}
					limit: 1
				) {
					cache_key
					response_json
					_additional { certainty }
				}
			}
		}`,
	}

	data, err := json.Marshal(gql)
	if err != nil {
		return nil
	}

	req, err := http.NewRequest("POST", w.baseURL+"/v1/graphql", bytes.NewReader(data))
	if err != nil {
		return nil
	}
	req.Header.Set("Content-Type", "application/json")
	w.setAuth(req)

	resp, err := w.client.Do(req)
	if err != nil {
		slog.Warn("weaviate search error", "error", err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil
	}

	var result struct {
		Data struct {
			Get map[string][]struct {
				ResponseJSON string `json:"response_json"`
				Additional   struct {
					Certainty float64 `json:"certainty"`
				} `json:"_additional"`
			} `json:"Get"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil
	}

	items := result.Data.Get[w.className]
	if len(items) == 0 {
		return nil
	}

	hit := items[0]
	var chatResp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(hit.ResponseJSON), &chatResp); err != nil {
		return nil
	}

	return &SearchResult{
		Response:   &chatResp,
		Similarity: hit.Additional.Certainty,
	}
}

func (w *WeaviateBackend) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	responseJSON, err := json.Marshal(resp)
	if err != nil {
		return
	}

	expiresAt := time.Now().Add(ttl).Unix()

	body := map[string]interface{}{
		"class":  w.className,
		"vector": vector,
		"properties": map[string]interface{}{
			"cache_key":     key,
			"model_name":    model,
			"response_json": string(responseJSON),
			"expires_at":    expiresAt,
		},
	}

	data, err := json.Marshal(body)
	if err != nil {
		return
	}

	req, err := http.NewRequest("POST", w.baseURL+"/v1/objects", bytes.NewReader(data))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	w.setAuth(req)

	r, err := w.client.Do(req)
	if err != nil {
		slog.Warn("weaviate upsert error", "error", err)
		return
	}
	r.Body.Close()
}

func (w *WeaviateBackend) Len() int {
	gql := map[string]interface{}{
		"query": `{ Aggregate { ` + w.className + ` { meta { count } } } }`,
	}
	data, _ := json.Marshal(gql)

	req, _ := http.NewRequest("POST", w.baseURL+"/v1/graphql", bytes.NewReader(data))
	if req == nil {
		return 0
	}
	req.Header.Set("Content-Type", "application/json")
	w.setAuth(req)

	resp, err := w.client.Do(req)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	var result struct {
		Data struct {
			Aggregate map[string][]struct {
				Meta struct {
					Count int `json:"count"`
				} `json:"meta"`
			} `json:"Aggregate"`
		} `json:"data"`
	}
	json.NewDecoder(resp.Body).Decode(&result)
	items := result.Data.Aggregate[w.className]
	if len(items) > 0 {
		return items[0].Meta.Count
	}
	return 0
}

func (w *WeaviateBackend) Dims() int {
	return w.dims
}

func (w *WeaviateBackend) ensureClass() {
	// Check if class exists.
	req, _ := http.NewRequest("GET", w.baseURL+"/v1/schema/"+w.className, nil)
	if req == nil {
		return
	}
	w.setAuth(req)
	resp, err := w.client.Do(req)
	if err != nil {
		return
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		return
	}

	// Create class.
	schema := map[string]interface{}{
		"class":      w.className,
		"vectorizer": "none",
		"properties": []map[string]interface{}{
			{"name": "cache_key", "dataType": []string{"text"}},
			{"name": "model_name", "dataType": []string{"text"}},
			{"name": "response_json", "dataType": []string{"text"}},
			{"name": "expires_at", "dataType": []string{"int"}},
		},
	}
	data, _ := json.Marshal(schema)
	req, _ = http.NewRequest("POST", w.baseURL+"/v1/schema", bytes.NewReader(data))
	if req == nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	w.setAuth(req)

	resp, err = w.client.Do(req)
	if err != nil {
		slog.Warn("weaviate create class error", "error", err)
		return
	}
	resp.Body.Close()
	slog.Info("weaviate class created", "class", w.className)
}

func (w *WeaviateBackend) setAuth(req *http.Request) {
	if w.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+w.apiKey)
	}
}

// Helper functions for building GraphQL queries.
func vectorJSON(v []float32) string {
	data, _ := json.Marshal(v)
	return string(data)
}

func floatStr(f float64) string {
	return json.Number(fmt.Sprintf("%.4f", f)).String()
}

func intStr(n int64) string {
	return json.Number(fmt.Sprintf("%d", n)).String()
}
