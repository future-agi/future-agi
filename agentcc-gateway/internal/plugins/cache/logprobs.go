package cache

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// logprobsVariant keeps the two wire contracts separate: native completions
// have token arrays and text offsets, chat has token records with bytes and
// refusal data. Neither can be converted losslessly into the other. A native
// logprobs=0 still requests the selected tokens' probabilities.
func logprobsVariant(rc *models.RequestContext) string {
	if count, ok := rc.Metadata["cache_native_logprobs"]; ok {
		return "completion:" + count
	}
	if rc.Request != nil && rc.Request.Logprobs != nil && *rc.Request.Logprobs {
		count := 0
		if rc.Request.TopLogprobs != nil {
			count = *rc.Request.TopLogprobs
		}
		return "chat:" + strconv.Itoa(count)
	}
	return ""
}

// qualifyLogprobsKey wraps both generated and caller-chosen keys, retaining
// their tenant/namespace partition. Unrequested logprobs keep the existing v2
// key space and cross-route reuse. Requested logprobs use a new domain so old
// entries without route/option provenance expire naturally, without migration.
func qualifyLogprobsKey(key string, rc *models.RequestContext) string {
	variant := logprobsVariant(rc)
	if variant == "" {
		return key
	}
	digest := sha256.Sum256([]byte("logprobs-v1\x00" + variant + "\x00" + key))
	return hex.EncodeToString(digest[:16])
}

// cacheResponse strips unrequested, protocol-specific data on both storage
// and retrieval (including pre-upgrade exact/semantic entries). Clone only
// when needed, and never mutate a backend's answer or the live wire response.
// Requested logprobs are preserved byte for byte in their qualified partition.
func cacheResponse(rc *models.RequestContext, response *models.ChatCompletionResponse) *models.ChatCompletionResponse {
	if response == nil || logprobsVariant(rc) != "" {
		return response
	}
	for _, choice := range response.Choices {
		if choice.Logprobs != nil {
			clean := *response
			clean.Choices = append([]models.Choice(nil), response.Choices...)
			for i := range clean.Choices {
				clean.Choices[i].Logprobs = nil
			}
			return &clean
		}
	}
	return response
}
