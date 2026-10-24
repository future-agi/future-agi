package models

import (
	"encoding/json"
	"strconv"
)

// ResponseStatusMetadataKey is the request metadata key under which a handler
// that passes the provider's answer through as it is, with no chat answer on
// the request context (a token count, a Google embedContent), records the
// status it answers the client with, before the post-plugins run, so the
// request log records it (logging's buildRecord). Caller metadata never
// sets it.
const ResponseStatusMetadataKey = "response_status"

// CountedTokensMetadataKey is the request metadata key under which a token
// count's handler records the count the provider returned. The count is not
// the request's usage: counting is free at the provider and consumes no
// tokens, so the request log keeps it here and its token fields at 0.
// Caller metadata never sets it.
const CountedTokensMetadataKey = "counted_tokens"

// RecordPassedThroughAnswer records on rc the status a handler answers with
// for an answer it passes through as the provider sent it, and, for a token
// count (counted), the count a successful answer reports: Anthropic's
// input_tokens or Google's totalTokens.
func RecordPassedThroughAnswer(rc *RequestContext, status int, body []byte, counted bool) {
	rc.SetMetadata(ResponseStatusMetadataKey, strconv.Itoa(status))
	if !counted || status >= 400 {
		return
	}
	var count struct {
		InputTokens *int `json:"input_tokens"`
		TotalTokens *int `json:"totalTokens"`
	}
	if json.Unmarshal(body, &count) != nil {
		return
	}
	switch {
	case count.InputTokens != nil:
		rc.SetMetadata(CountedTokensMetadataKey, strconv.Itoa(*count.InputTokens))
	case count.TotalTokens != nil:
		rc.SetMetadata(CountedTokensMetadataKey, strconv.Itoa(*count.TotalTokens))
	}
}
