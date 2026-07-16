package cache

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// QdrantBackend provides semantic caching via Qdrant vector database.
type QdrantBackend struct {
	client     *http.Client
	baseURL    string
	collection string
	apiKey     string
	threshold  float64
	dims       int
}

// NewQdrantBackend creates a Qdrant-backed semantic cache.
func NewQdrantBackend(baseURL, collection, apiKey string, threshold float64, dims int, timeout time.Duration) (*QdrantBackend, error) {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	if threshold <= 0 || threshold > 1 {
		threshold = 0.85
	}
	if dims <= 0 {
		dims = 256
	}

	q := &QdrantBackend{
		client:     &http.Client{Timeout: timeout},
		baseURL:    baseURL,
		collection: collection,
		apiKey:     apiKey,
		threshold:  threshold,
		dims:       dims,
	}

	// Ensure collection exists.
	if err := q.ensureCollection(); err != nil {
		slog.Warn("qdrant: failed to ensure collection, will retry on first use", "error", err)
	}

	return q, nil
}

func (q *QdrantBackend) Search(vector []float32, model string) *SearchResult {
	nowUnix := time.Now().Unix()

	body := map[string]interface{}{
		"vector": vector,
		"filter": map[string]interface{}{
			"must": []map[string]interface{}{
				{
					"key":   "model",
					"match": map[string]interface{}{"value": model},
				},
				{
					"key":   "expires_at",
					"range": map[string]interface{}{"gt": nowUnix},
				},
			},
		},
		"limit":           1,
		"score_threshold": q.threshold,
		"with_payload":    true,
	}

	data, err := json.Marshal(body)
	if err != nil {
		return nil
	}

	req, err := http.NewRequest("POST", q.baseURL+"/collections/"+q.collection+"/points/search", bytes.NewReader(data))
	if err != nil {
		return nil
	}
	req.Header.Set("Content-Type", "application/json")
	q.setAuth(req)

	resp, err := q.client.Do(req)
	if err != nil {
		slog.Warn("qdrant search error", "error", err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil
	}

	var result struct {
		Result []struct {
			Score   float64                `json:"score"`
			Payload map[string]interface{} `json:"payload"`
		} `json:"result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil
	}

	if len(result.Result) == 0 {
		return nil
	}

	hit := result.Result[0]
	responseJSON, ok := hit.Payload["response"].(string)
	if !ok {
		return nil
	}

	var chatResp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(responseJSON), &chatResp); err != nil {
		return nil
	}

	return &SearchResult{
		Response:   &chatResp,
		Similarity: hit.Score,
	}
}

func (q *QdrantBackend) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	responseJSON, err := json.Marshal(resp)
	if err != nil {
		return
	}

	expiresAt := time.Now().Add(ttl).Unix()

	body := map[string]interface{}{
		"points": []map[string]interface{}{
			{
				"id":     key,
				"vector": vector,
				"payload": map[string]interface{}{
					"model":      model,
					"cache_key":  key,
					"response":   string(responseJSON),
					"expires_at": expiresAt,
				},
			},
		},
	}

	data, err := json.Marshal(body)
	if err != nil {
		return
	}

	req, err := http.NewRequest("PUT", q.baseURL+"/collections/"+q.collection+"/points?wait=false", bytes.NewReader(data))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	q.setAuth(req)

	r, err := q.client.Do(req)
	if err != nil {
		slog.Warn("qdrant upsert error", "error", err)
		return
	}
	r.Body.Close()
}

func (q *QdrantBackend) Len() int {
	req, err := http.NewRequest("GET", q.baseURL+"/collections/"+q.collection, nil)
	if err != nil {
		return 0
	}
	q.setAuth(req)

	resp, err := q.client.Do(req)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	var result struct {
		Result struct {
			PointsCount int `json:"points_count"`
		} `json:"result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0
	}
	return result.Result.PointsCount
}

func (q *QdrantBackend) Dims() int {
	return q.dims
}

func (q *QdrantBackend) ensureCollection() error {
	// Check if collection exists.
	req, err := http.NewRequest("GET", q.baseURL+"/collections/"+q.collection, nil)
	if err != nil {
		return err
	}
	q.setAuth(req)

	resp, err := q.client.Do(req)
	if err != nil {
		return err
	}
	resp.Body.Close()

	if resp.StatusCode == http.StatusOK {
		return nil // collection exists
	}

	// Create collection.
	body := map[string]interface{}{
		"vectors": map[string]interface{}{
			"size":     q.dims,
			"distance": "Cosine",
		},
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err = http.NewRequest("PUT", q.baseURL+"/collections/"+q.collection, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	q.setAuth(req)

	resp, err = q.client.Do(req)
	if err != nil {
		return err
	}
	body2, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("create collection: status %d: %s", resp.StatusCode, body2)
	}

	slog.Info("qdrant collection created", "collection", q.collection, "dims", q.dims)
	return nil
}

func (q *QdrantBackend) setAuth(req *http.Request) {
	if q.apiKey != "" {
		req.Header.Set("Api-Key", q.apiKey)
	}
}
