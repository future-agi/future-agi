// Package runtimesupport serves revision-bound, metadata-only connector
// capability observations. It never authorizes an OAuth or provider action.
package runtimesupport

import (
	"crypto/subtle"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

const (
	Path           = "/.well-known/connector-runtime-support"
	RequestSchema  = "connector-runtime-support-request-v1"
	ResponseSchema = "connector-runtime-support-response-v1"
	maxBody        = 16384
)

var (
	appPattern      = regexp.MustCompile(`^[a-z][a-z0-9-]{1,39}$`)
	providerPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{1,23}$`)
	revisionPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\+sha256:[0-9a-f]{64}$`)
	noncePattern    = regexp.MustCompile(`^[0-9a-f]{32}$`)
	digestPattern   = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// Request retains the six-field wire shape under PRODUCER-CONTRACT-r2. In particular, operations
// are NOT a request field: the canonical record declares the required set.
// The response returns the confirmed set; the consumer does its own subset
// check. No org, grant, client registration or credential belongs here.
type Request struct {
	Schema     string `json:"schema"`
	AppID      string `json:"app_id"`
	ProviderID string `json:"provider_id"`
	Revision   string `json:"revision"`
	RuntimeURL string `json:"runtime_url"`
	RequestID  string `json:"request_id"`
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	// Same Bearer admin channel as GatewayAdminClient/checkAdminAuth. An empty
	// configuration never enables anonymous access; duplicate headers refuse.
	if strings.TrimSpace(h.adminToken) == "" || len(r.Header.Values("Authorization")) != 1 ||
		subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+h.adminToken)) != 1 {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeError(w, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	encodings := r.Header.Values("Content-Encoding")
	invalidEncoding := len(encodings) > 1
	for _, encoding := range encodings {
		if encoding != "" {
			invalidEncoding = true
		}
	}
	media, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || media != "application/json" || len(params) > 1 || (len(params) == 1 && !strings.EqualFold(params["charset"], "utf-8")) || invalidEncoding {
		writeError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	req, ok := parseRequest(raw)
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	answer := h.answer(r.Context(), req)
	_ = json.NewEncoder(w).Encode(answer)
}

func writeError(w http.ResponseWriter, status int, code string) {
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code})
}

func parseRequest(raw []byte) (Request, bool) {
	obj, err := strictObject(raw)
	if err != nil || len(obj) != 6 {
		return Request{}, false
	}
	req := Request{}
	for field, dst := range map[string]*string{"schema": &req.Schema, "app_id": &req.AppID, "provider_id": &req.ProviderID, "revision": &req.Revision, "runtime_url": &req.RuntimeURL, "request_id": &req.RequestID} {
		value, ok := obj[field].(string)
		if !ok {
			return Request{}, false
		}
		*dst = value
	}
	return req, req.Schema == RequestSchema && appPattern.MatchString(req.AppID) && providerPattern.MatchString(req.ProviderID) &&
		len(req.Revision) <= 256 && revisionPattern.MatchString(req.Revision) && noncePattern.MatchString(req.RequestID) && validBase(req.RuntimeURL)
}

func validBase(raw string) bool {
	if len(raw) == 0 || len(raw) > 2048 || strings.HasSuffix(raw, "/") || strings.ContainsAny(raw, "\\?#") {
		return false
	}
	// Do not silently normalize whitespace, Unicode hostnames, userinfo or
	// malformed ports into the configured audience. The URL is never fetched.
	for _, c := range raw {
		if c <= 0x20 || c >= 0x7f {
			return false
		}
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Opaque != "" || u.ForceQuery || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	if strings.HasSuffix(u.Host, ":") {
		return false
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return false
		}
	}
	return true
}

func responseIdentity(req Request, state string) map[string]any {
	return map[string]any{"schema": ResponseSchema, "state": state, "app_id": req.AppID, "provider_id": req.ProviderID,
		"revision": req.Revision, "runtime_url": req.RuntimeURL, "request_id": req.RequestID}
}

func unavailable(req Request, reason string) map[string]any {
	body := responseIdentity(req, "unavailable")
	body["reason_code"] = reason
	return body
}
