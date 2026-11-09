package common

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// NativeHTTPError records an actual HTTP refusal at a native stream handshake.
// credentials are the request-bound values, not a later token-provider lookup.
func NativeHTTPError(status int, body []byte, credentials ...string) *models.APIError {
	var envelope struct {
		Error struct {
			Message string          `json:"message"`
			Type    string          `json:"type"`
			Status  string          `json:"status"`
			Code    json.RawMessage `json:"code"`
		} `json:"error"`
	}
	e := ExactAPIError(&models.APIError{Status: status, UpstreamStatus: status, ProviderStatus: status}, credentials...)
	if status >= http.StatusInternalServerError {
		e.Status = http.StatusBadGateway
	}
	if json.Unmarshal(body, &envelope) == nil {
		e.Message = privacy.ScrubExactCredential(envelope.Error.Message, credentials...)
		e.Type = privacy.ScrubExactCredential(envelope.Error.Type, credentials...)
		if e.Type == "" {
			e.Type = privacy.ScrubExactCredential(envelope.Error.Status, credentials...)
		}
		var code string
		_ = json.Unmarshal(envelope.Error.Code, &code)
		e.Code = privacy.ScrubExactCredential(code, credentials...)
	}
	if e.Message == "" {
		// Mask before truncation; never create a new escaped fragment by cutting a key.
		e.Message = privacy.ScrubExactCredential(string(body), credentials...)
		if len(e.Message) > 500 {
			e.Message = e.Message[:500] + "..."
		}
	}
	e.Message = models.RedactCredentials(e.Message)
	return e
}

// ExactAPIError is the single typed boundary shared by every adapter. The copy
// retains status, retry, rejection, ownership and billing evidence; only public
// diagnostic fields change. It must run before an error is logged or escapes.
func ExactAPIError(e *models.APIError, credentials ...string) *models.APIError {
	if e == nil {
		return nil
	}
	out := *e
	out.Message = privacy.ScrubExactCredential(e.Message, credentials...)
	out.Code = privacy.ScrubExactCredential(e.Code, credentials...)
	out.Type = privacy.ScrubExactCredential(e.Type, credentials...)
	return &out
}

// ExactError sanitizes non-API transport/decoder wrappers as well. Do not expose
// the original with Unwrap: otherwise a downstream errors.As/log can recover
// its secret-bearing text. Is and timeout classification remain available.
func ExactError(err error, credentials ...string) error {
	if err == nil {
		return nil
	}
	if e, ok := err.(*models.APIError); ok {
		return ExactAPIError(e, credentials...)
	}
	text := privacy.ScrubExactCredential(err.Error(), credentials...)
	var api *models.APIError
	errors.As(err, &api)
	return &exactError{text: text, source: err, api: ExactAPIError(api, credentials...), timeout: timedOut(err), transport: IsTransportError(err)}
}

// IsTransportError preserves classification without exposing a sanitized
// wrapper's original URL or diagnostics through errors.As/Unwrap.
func IsTransportError(err error) bool {
	var exact *exactError
	if errors.As(err, &exact) {
		return exact.transport
	}
	var transport *url.Error
	return errors.As(err, &transport)
}

type exactError struct {
	text      string
	source    error
	api       *models.APIError
	timeout   bool
	transport bool
}

func (e *exactError) Error() string        { return e.text }
func (e *exactError) Is(target error) bool { return errors.Is(e.source, target) }
func (e *exactError) Timeout() bool        { return e.timeout }
func (e *exactError) Temporary() bool      { return e.timeout }
func (e *exactError) As(target any) bool {
	switch t := target.(type) {
	case **models.APIError:
		if e.api == nil {
			return false
		}
		*t = e.api
		return true
	case **netguard.BlockedError:
		// A guarded-dialer refusal is classification only: its text never
		// carries the destination or any credential, so keep it findable.
		return errors.As(e.source, t)
	}
	return false
}

// ErrorCredentials snapshots the actual outbound request, including overridden
// headers and refreshed Vertex tokens. No shared last-request state is used.
// held values add credentials retained by the instance (e.g. AWS signing key).
func ErrorCredentials(req *http.Request, held ...string) []string {
	out := append([]string(nil), held...)
	if req == nil {
		return out
	}
	for name, values := range req.Header {
		n := strings.ToLower(name)
		if n != "cookie" && n != "authorization" && n != "proxy-authorization" && !strings.Contains(n, "key") && !strings.Contains(n, "token") && !strings.Contains(n, "secret") && !strings.Contains(n, "session") && !strings.Contains(n, "signature") {
			continue
		}
		for _, v := range values {
			if v == "" {
				continue
			}
			out = append(out, v)
			if n == "authorization" || n == "proxy-authorization" {
				scheme, token, ok := strings.Cut(v, " ")
				if ok && token != "" {
					out = append(out, token)
					if strings.EqualFold(scheme, "Basic") {
						if decoded, err := base64.StdEncoding.DecodeString(token); err == nil {
							out = append(out, string(decoded))
							if _, pass, ok := strings.Cut(string(decoded), ":"); ok {
								out = append(out, pass)
							}
						}
					}
					if strings.HasPrefix(scheme, "AWS") {
						for _, part := range strings.Split(token, ",") {
							k, value, ok := strings.Cut(strings.TrimSpace(part), "=")
							if !ok {
								continue
							}
							if k == "Credential" {
								id, _, _ := strings.Cut(value, "/")
								out = append(out, id)
							}
							if k == "Signature" {
								out = append(out, value)
							}
						}
					}
				}
			}
			if n == "cookie" {
				for _, part := range strings.Split(v, ";") {
					if _, value, ok := strings.Cut(strings.TrimSpace(part), "="); ok {
						out = append(out, value)
					}
				}
			}
		}
	}
	if req.URL != nil && req.URL.User != nil {
		if pass, ok := req.URL.User.Password(); ok {
			out = append(out, pass)
		}
		out = append(out, req.URL.User.String())
	}
	return out
}

// HeldErrorCredentials copies only credential-bearing configured headers. It is
// also used before a request can be constructed, when no request exists yet.
func HeldErrorCredentials(headers map[string]string, held ...string) []string {
	h := make(http.Header, len(headers))
	for name, value := range headers {
		h.Set(name, value)
	}
	return ErrorCredentials(&http.Request{Header: h}, held...)
}

// ErrorCredentials exposes no mutable header map to an adapter or a caller.
func (h HeaderDefaults) ErrorCredentials(held ...string) []string {
	return HeldErrorCredentials(h.values, held...)
}
