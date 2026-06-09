package cache

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// PineconeBackend provides semantic caching via Pinecone vector database.
type PineconeBackend struct {
	client    *http.Client
	baseURL   string // e.g. "https://index-name-projectid.svc.environment.pinecone.io"
	apiKey    string
	threshold float64
	dims      int
}

// NewPineconeBackend creates a Pinecone-backed semantic cache.
func NewPineconeBackend(baseURL, apiKey string, threshold float64, dims int, timeout time.Duration) (*PineconeBackend, error) {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	if threshold <= 0 || threshold > 1 {
		threshold = 0.85
	}
	if dims <= 0 {
		dims = 256
	}

	return &PineconeBackend{
		client:    &http.Client{Timeout: timeout},
		baseURL:   baseURL,
		apiKey:    apiKey,
		threshold: threshold,
		dims:      dims,
	}, nil
}

func (p *PineconeBackend) Search(vector []float32, model string) *SearchResult {
	nowUnix := time.Now().Unix()

	body := map[string]interface{}{
		"vector":          vector,
		"topK":            1,
		"includeMetadata": true,
		"filter": map[string]interface{}{
			"model": map[string]interface{}{
				"$eq": model,
			},
			"expires_at": map[string]interface{}{
				"$gt": nowUnix,
			},
		},
	}

	data, err := json.Marshal(body)
	if err != nil {
		return nil
	}

	req, err := http.NewRequest("POST", p.baseURL+"/query", bytes.NewReader(data))
	if err != nil {
		return nil
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Api-Key", p.apiKey)

	resp, err := p.client.Do(req)
	if err != nil {
		slog.Warn("pinecone search error", "error", err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil
	}

	var result struct {
		Matches []struct {
			Score    float64                `json:"score"`
			Metadata map[string]interface{} `json:"metadata"`
		} `json:"matches"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil
	}

	if len(result.Matches) == 0 || result.Matches[0].Score < p.threshold {
		return nil
	}

	hit := result.Matches[0]
	responseJSON, ok := hit.Metadata["response"].(string)
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

func (p *PineconeBackend) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	responseJSON, err := json.Marshal(resp)
	if err != nil {
		return
	}

	expiresAt := time.Now().Add(ttl).Unix()

	body := map[string]interface{}{
		"vectors": []map[string]interface{}{
			{
				"id":     key,
				"values": vector,
				"metadata": map[string]interface{}{
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

	req, err := http.NewRequest("POST", p.baseURL+"/vectors/upsert", bytes.NewReader(data))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Api-Key", p.apiKey)

	r, err := p.client.Do(req)
	if err != nil {
		slog.Warn("pinecone upsert error", "error", err)
		return
	}
	r.Body.Close()
}

func (p *PineconeBackend) Len() int {
	req, _ := http.NewRequest("GET", p.baseURL+"/describe_index_stats", nil)
	if req == nil {
		return 0
	}
	req.Header.Set("Api-Key", p.apiKey)

	resp, err := p.client.Do(req)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	var result struct {
		TotalVectorCount int `json:"totalVectorCount"`
	}
	json.NewDecoder(resp.Body).Decode(&result)
	return result.TotalVectorCount
}

func (p *PineconeBackend) Dims() int {
	return p.dims
}
