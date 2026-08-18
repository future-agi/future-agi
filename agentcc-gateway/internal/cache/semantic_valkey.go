package cache

import (
	"context"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/valkey-io/valkey-go"
)

// ValkeyBackend provides semantic caching via Valkey's vector search module.
// It uses HNSW indexing for approximate nearest-neighbor search on cached embeddings.
type ValkeyBackend struct {
	client    valkey.Client
	index     string
	prefix    string
	threshold float64
	dims      int
	timeout   time.Duration
}

// NewValkeyBackend creates a Valkey-backed semantic cache.
// Requires Valkey 8.0+ with the valkey-search module enabled.
func NewValkeyBackend(address, password, index, prefix string, threshold float64, dims int, timeout time.Duration, useTLS bool) (*ValkeyBackend, error) {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	if threshold <= 0 || threshold > 1 {
		threshold = 0.85
	}
	if dims <= 0 {
		dims = 256
	}
	if index == "" {
		index = "agentcc_semantic_cache"
	}
	if prefix == "" {
		prefix = "sc:"
	}

	opts, err := valkey.ParseURL(address)
	if err != nil {
		slog.Debug("valkey: ParseURL failed, using raw address", "address", address, "error", err)
		opts = valkey.ClientOption{
			InitAddress: []string{address},
			Password:    password,
		}
	} else {
		if password != "" {
			opts.Password = password
		}
	}
	opts.Dialer = net.Dialer{Timeout: timeout}
	opts.ConnWriteTimeout = timeout

	if useTLS {
		opts.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}

	client, err := valkey.NewClient(opts)
	if err != nil {
		return nil, fmt.Errorf("valkey connect: %w", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	if err := client.Do(ctx, client.B().Ping().Build()).Error(); err != nil {
		client.Close()
		return nil, fmt.Errorf("valkey ping: %w", err)
	}

	v := &ValkeyBackend{
		client:    client,
		index:     index,
		prefix:    prefix,
		threshold: threshold,
		dims:      dims,
		timeout:   timeout,
	}

	if err := v.ensureIndex(ctx); err != nil {
		client.Close()
		return nil, fmt.Errorf("valkey: failed to create search index: %w", err)
	}

	return v, nil
}

func (v *ValkeyBackend) Search(vector []float32, model string) *SearchResult {
	ctx, cancel := context.WithTimeout(context.Background(), v.timeout)
	defer cancel()

	blob := float32SliceToBytes(vector)
	nowUnix := time.Now().Unix()

	query := fmt.Sprintf("(@model:{%s} @expires_at:[%d +inf])=>[KNN 1 @vector $BLOB AS score]",
		escapeTag(model), nowUnix)

	cmd := client_FtSearch(v.client, v.index, query, blob)
	resp := v.client.Do(ctx, cmd)
	if resp.Error() != nil {
		slog.Debug("valkey semantic search error", "error", resp.Error())
		return nil
	}

	results, err := parseFtSearchResponse(resp)
	if err != nil || len(results) == 0 {
		return nil
	}

	hit := results[0]
	similarity := 1.0 - hit.score
	if similarity < 0 {
		similarity = 0
	}

	if similarity < v.threshold {
		return nil
	}

	responseJSON, ok := hit.fields["response"]
	if !ok {
		return nil
	}

	var chatResp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(responseJSON), &chatResp); err != nil {
		return nil
	}

	return &SearchResult{
		Response:   &chatResp,
		Similarity: similarity,
	}
}

func (v *ValkeyBackend) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	responseJSON, err := json.Marshal(resp)
	if err != nil {
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), v.timeout)
	defer cancel()

	expiresAt := time.Now().Add(ttl).Unix()
	blob := float32SliceToBytes(vector)
	hashKey := v.prefix + key

	// Build HSET command with all fields including the binary vector blob.
	// Go strings can contain arbitrary bytes, so string(blob) preserves the binary content.
	hsetCmd := v.client.B().Arbitrary("HSET", hashKey,
		"model", model,
		"response", string(responseJSON),
		"expires_at", strconv.FormatInt(expiresAt, 10),
		"vector", string(blob),
	).Build()

	// Use PEXPIRE with milliseconds to avoid truncating sub-second TTLs to 0.
	pexpireCmd := v.client.B().Pexpire().Key(hashKey).Milliseconds(ttl.Milliseconds()).Build()

	for _, r := range v.client.DoMulti(ctx, hsetCmd, pexpireCmd) {
		if r.Error() != nil {
			slog.Warn("valkey semantic set error", "key", key, "error", r.Error())
			return
		}
	}
}

func (v *ValkeyBackend) Len() int {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	cmd := v.client.B().Arbitrary("FT.INFO", v.index).Build()
	resp := v.client.Do(ctx, cmd)
	if resp.Error() != nil {
		return 0
	}

	// RESP3: FT.INFO returns a map.
	m, err := resp.AsMap()
	if err == nil {
		if numDocs, ok := m["num_docs"]; ok {
			n, _ := numDocs.AsInt64()
			return int(n)
		}
		return 0
	}

	// Fallback for RESP2: array of key-value pairs.
	arr, err := resp.ToArray()
	if err != nil {
		return 0
	}
	for i := 0; i < len(arr)-1; i++ {
		key, _ := arr[i].ToString()
		if key == "num_docs" {
			val, _ := arr[i+1].ToString()
			n, _ := strconv.Atoi(val)
			return n
		}
	}
	return 0
}

func (v *ValkeyBackend) Dims() int {
	return v.dims
}

func (v *ValkeyBackend) Close() {
	v.client.Close()
}

func (v *ValkeyBackend) ensureIndex(ctx context.Context) error {
	infoCmd := v.client.B().Arbitrary("FT.INFO", v.index).Build()
	if v.client.Do(ctx, infoCmd).Error() == nil {
		return nil
	}

	createArgs := []string{
		"FT.CREATE", v.index,
		"ON", "HASH",
		"PREFIX", "1", v.prefix,
		"SCHEMA",
		"model", "TAG",
		"expires_at", "NUMERIC", "SORTABLE",
		"vector", "VECTOR", "HNSW", "6",
		"TYPE", "FLOAT32",
		"DIM", strconv.Itoa(v.dims),
		"DISTANCE_METRIC", "COSINE",
	}

	cmd := v.client.B().Arbitrary(createArgs...).Build()
	if err := v.client.Do(ctx, cmd).Error(); err != nil {
		return fmt.Errorf("FT.CREATE: %w", err)
	}

	slog.Info("valkey search index created", "index", v.index, "dims", v.dims, "prefix", v.prefix)
	return nil
}

type ftSearchHit struct {
	key    string
	score  float64
	fields map[string]string
}

const scoreFieldName = "score"

func parseFtSearchResponse(resp valkey.ValkeyResult) ([]ftSearchHit, error) {
	// RESP3 format: response is a map with "total_results" and "results" keys.
	respMap, err := resp.AsMap()
	if err != nil {
		return nil, fmt.Errorf("parse FT.SEARCH response: %w", err)
	}

	totalResult, ok := respMap["total_results"]
	if !ok {
		return nil, nil
	}
	total, _ := totalResult.AsInt64()
	if total == 0 {
		return nil, nil
	}

	resultsVal, ok := respMap["results"]
	if !ok {
		return nil, nil
	}
	resultsArr, err := resultsVal.ToArray()
	if err != nil {
		return nil, fmt.Errorf("parse results array: %w", err)
	}

	var hits []ftSearchHit
	for _, item := range resultsArr {
		itemMap, err := item.AsMap()
		if err != nil {
			continue
		}

		key := ""
		if idVal, ok := itemMap["id"]; ok {
			key, _ = idVal.ToString()
		}

		fields := make(map[string]string)

		// Fields are in "extra_attributes" map in RESP3.
		if attrsVal, ok := itemMap["extra_attributes"]; ok {
			attrsMap, err := attrsVal.AsMap()
			if err == nil {
				for k, v := range attrsMap {
					s, _ := v.ToString()
					fields[k] = s
				}
			}
		}

		score := 0.0
		if s, ok := fields[scoreFieldName]; ok {
			score, _ = strconv.ParseFloat(s, 64)
			delete(fields, scoreFieldName)
		}

		hits = append(hits, ftSearchHit{
			key:    key,
			score:  score,
			fields: fields,
		})
	}

	return hits, nil
}

func client_FtSearch(c valkey.Client, index, query string, blob []byte) valkey.Completed {
	return c.B().Arbitrary("FT.SEARCH", index, query,
		"RETURN", "4", "model", "response", "expires_at", "score",
		"PARAMS", "2", "BLOB", string(blob),
		"SORTBY", "score", "ASC",
		"LIMIT", "0", "1",
		"DIALECT", "2",
	).Build()
}

func float32SliceToBytes(v []float32) []byte {
	buf := make([]byte, len(v)*4)
	for i, f := range v {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(f))
	}
	return buf
}

func escapeTag(s string) string {
	replacer := strings.NewReplacer(
		"\\", "\\\\",
		"|", "\\|",
		",", "\\,",
		".", "\\.",
		"<", "\\<",
		">", "\\>",
		"{", "\\{",
		"}", "\\}",
		"[", "\\[",
		"]", "\\]",
		"\"", "\\\"",
		"'", "\\'",
		":", "\\:",
		";", "\\;",
		"!", "\\!",
		"@", "\\@",
		"#", "\\#",
		"$", "\\$",
		"%", "\\%",
		"^", "\\^",
		"&", "\\&",
		"*", "\\*",
		"(", "\\(",
		")", "\\)",
		"-", "\\-",
		"+", "\\+",
		"=", "\\=",
		"~", "\\~",
		"/", "\\/",
		" ", "\\ ",
	)
	return replacer.Replace(s)
}
