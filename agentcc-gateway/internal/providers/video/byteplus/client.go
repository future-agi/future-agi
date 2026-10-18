package byteplus

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

const maxResponseBytes = 2 << 20

func validID(s string) bool {
	if s == "" || len(s) > 256 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}
func requestID(h http.Header) string {
	if v := h.Get("X-Request-Id"); v != "" {
		return v
	}
	return h.Get("X-Tt-Logid")
}
func (a *Adapter) call(ctx context.Context, method, id string, query url.Values, payload any, timeout time.Duration, out any) ([]byte, http.Header, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	u := *a.base
	u.Path = strings.TrimRight(u.Path, "/") + "/contents/generations/tasks"
	if id != "" {
		if !validID(id) {
			return nil, nil, &video.SchemaError{Field: "provider_job_id"}
		}
		u.Path += "/" + id
	}
	u.RawPath = ""
	u.RawQuery = query.Encode()
	var body io.Reader
	if payload != nil {
		b, e := json.Marshal(payload)
		if e != nil {
			return nil, nil, e
		}
		body = bytes.NewReader(b)
	}
	req, e := http.NewRequestWithContext(ctx, method, u.String(), body)
	if e != nil {
		return nil, nil, &video.SchemaError{Field: "request"}
	}
	req.Header.Set("Authorization", "Bearer "+a.cfg.Provider.APIKey)
	req.Header.Set("Accept", "application/json")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, e := a.api.Do(req)
	if e != nil {
		return nil, nil, transportError(ctx, e)
	}
	defer resp.Body.Close()
	raw, e := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if e != nil {
		return nil, resp.Header, transportError(ctx, e)
	}
	if len(raw) > maxResponseBytes {
		return nil, resp.Header, &video.SchemaError{Field: "response.size"}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.Header, upstreamError(resp, raw, method == http.MethodPost)
	}
	if out != nil {
		if e = json.Unmarshal(raw, out); e != nil {
			return nil, resp.Header, &video.SchemaError{Field: "response", Cause: e}
		}
	}
	return raw, resp.Header, nil
}
func upstreamError(r *http.Response, body []byte, submit bool) *video.UpstreamError {
	code := "provider_request_failed"
	retry := false
	switch {
	case r.StatusCode == 401 || r.StatusCode == 403:
		code = "provider_access_denied"
	case r.StatusCode == 429:
		code = "provider_rate_limited"
		retry = true
	case r.StatusCode >= 500:
		code = "provider_unavailable"
		retry = true
	case r.StatusCode >= 400 && submit:
		code = "submit_rejected"
	}
	return &video.UpstreamError{Status: r.StatusCode, Code: code, RequestID: requestID(r.Header), Retryable: retry, RetryAfter: retryAfter(r.Header.Get("Retry-After"), time.Now()), Body: body}
}
func transportError(ctx context.Context, e error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var blocked *netguard.BlockedError
	if errors.As(e, &blocked) {
		return fmt.Errorf("provider destination refused")
	}
	return &video.UpstreamError{Code: "provider_transport_error", Retryable: true}
}
func retryAfter(s string, now time.Time) time.Duration {
	if n, e := strconv.ParseInt(s, 10, 64); e == nil {
		if n <= 0 {
			return 0
		}
		if n > int64((1<<63-1)/time.Second) {
			return time.Duration(1<<63 - 1)
		}
		return time.Duration(n) * time.Second
	}
	if t, e := http.ParseTime(s); e == nil && t.After(now) {
		return t.Sub(now)
	}
	return 0
}
