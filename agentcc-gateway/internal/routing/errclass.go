package routing

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// MaxAttemptErrorMessage is the most characters an attempt's error_message
// keeps.
const MaxAttemptErrorMessage = 512

// ClassifyError is the error class of a failed attempt and the side its error
// came from. status is an upstream HTTP status known apart
// from err, 0 when there is none; it takes precedence over the status err
// carries. A nil err with no error status has no class.
func ClassifyError(err error, status int) (models.ErrorClass, models.ErrorOrigin) {
	if err == nil {
		if status >= http.StatusBadRequest {
			return classifyStatus(status), models.OriginUpstream
		}
		return "", ""
	}
	if reason, _, ok := SkippedHop(err); ok {
		if reason == models.ReasonSkippedInsufficientCredits {
			return models.ErrClassInsufficientCredits, models.OriginGateway
		}
		return models.ErrClassPolicy, models.OriginGateway
	}
	if errors.Is(err, ErrCircuitOpen) {
		return models.ErrClassCircuitOpen, models.OriginGateway
	}
	if errors.Is(err, context.Canceled) {
		return models.ErrClassCancelled, models.OriginClient
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
		return models.ErrClassTimeout, models.OriginGateway
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return classifyAPIError(apiErr, status)
	}
	var netErr net.Error
	if errors.As(err, &netErr) {
		if netErr.Timeout() {
			return models.ErrClassTimeout, models.OriginUpstream
		}
		return models.ErrClassNetwork, models.OriginUpstream
	}
	if status >= http.StatusBadRequest {
		return classifyStatus(status), models.OriginUpstream
	}
	return models.ErrClassUnknown, models.OriginGateway
}

// classifyAPIError classifies an error the gateway or a provider adapter
// built. Adapters keep the upstream's status for a parsed error body (5xx
// mapped to 502), and put it in the code ("provider_<status>", 0 when the
// request never got an answer) when the body did not parse.
func classifyAPIError(e *models.APIError, status int) (models.ErrorClass, models.ErrorOrigin) {
	if e.Type == models.ErrTypeGatewayTimeout {
		return models.ErrClassTimeout, models.OriginGateway
	}
	if status == 0 {
		status = e.Status
		if n, ok := upstreamStatusCode(e); ok {
			if n == 0 {
				return models.ErrClassNetwork, models.OriginUpstream
			}
			status = n
		}
	}
	if status == http.StatusBadRequest || status == http.StatusRequestEntityTooLarge || status == http.StatusUnprocessableEntity {
		if isContentFilter(e) {
			return models.ErrClassContentFilter, models.OriginUpstream
		}
		if isContextLength(e) {
			return models.ErrClassContextLength, models.OriginUpstream
		}
	}
	if status < http.StatusBadRequest {
		return models.ErrClassUnknown, models.OriginUpstream
	}
	return classifyStatus(status), models.OriginUpstream
}

// upstreamStatusCode is the upstream's own status that an adapter kept in
// e's code ("provider_<status>") because the error body did not parse, 0 when
// the request never got an answer. ok is false when the code carries none.
func upstreamStatusCode(e *models.APIError) (status int, ok bool) {
	upstream, found := strings.CutPrefix(e.Code, "provider_")
	if !found {
		return 0, false
	}
	n, err := strconv.Atoi(upstream)
	if err != nil {
		return 0, false
	}
	return n, true
}

// providerStatusCodes is the HTTP status behind an adapter's named error
// code, for the adapters whose parsed error keeps the provider's error type
// in the code rather than its status: Anthropic's error types (its errors
// reference) and Google's RPC statuses (the HTTP mapping of google.rpc.Code).
var providerStatusCodes = map[string]int{
	"provider_api_error":          http.StatusInternalServerError,
	"provider_overloaded_error":   529,
	"provider_rate_limit_error":   http.StatusTooManyRequests,
	"provider_UNAVAILABLE":        http.StatusServiceUnavailable,
	"provider_INTERNAL":           http.StatusInternalServerError,
	"provider_UNKNOWN":            http.StatusInternalServerError,
	"provider_DATA_LOSS":          http.StatusInternalServerError,
	"provider_DEADLINE_EXCEEDED":  http.StatusGatewayTimeout,
	"provider_RESOURCE_EXHAUSTED": http.StatusTooManyRequests,
	"provider_UNIMPLEMENTED":      http.StatusNotImplemented,
}

// upstreamStatusOf is the HTTP status the provider itself answered e with,
// 0 when it is unknown: the status the adapter kept on the error, else the
// one in its code ("provider_<status>", or a named code above). Status lists
// match it beside e.Status, so a list naming the provider's
// 503 matches the 502 the gateway answers for it.
func upstreamStatusOf(e *models.APIError) int {
	if e.UpstreamStatus > 0 {
		return e.UpstreamStatus
	}
	if n, ok := upstreamStatusCode(e); ok {
		return n
	}
	return providerStatusCodes[e.Code]
}

// matchesStatus reports whether codes names e's status or its upstream's.
func matchesStatus(codes map[int]bool, e *models.APIError) bool {
	if codes[e.Status] {
		return true
	}
	if up := upstreamStatusOf(e); up > 0 && up != e.Status {
		return codes[up]
	}
	return false
}

// isProviderTimeout reports whether err says the provider did not answer
// in time: the gateway's timeout (the adapters' ErrGatewayTimeout), a 408 or
// 504 from the gateway or the provider, or a context, network or I/O
// deadline. The on_timeout switches act on it.
func isProviderTimeout(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		if apiErr.Type == models.ErrTypeGatewayTimeout {
			return true
		}
		switch apiErr.Status {
		case http.StatusRequestTimeout, http.StatusGatewayTimeout:
			return true
		}
		switch upstreamStatusOf(apiErr) {
		case http.StatusRequestTimeout, http.StatusGatewayTimeout:
			return true
		}
		return false
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

// classifyStatus is the class of an upstream error status.
func classifyStatus(status int) models.ErrorClass {
	switch {
	case status == http.StatusTooManyRequests:
		return models.ErrClassRateLimited
	case status == http.StatusRequestTimeout || status == http.StatusGatewayTimeout:
		return models.ErrClassTimeout
	case status >= http.StatusInternalServerError:
		return models.ErrClassUpstream5xx
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return models.ErrClassAuth
	case status == http.StatusPaymentRequired:
		return models.ErrClassInsufficientCredits
	case status == http.StatusBadRequest || status == http.StatusUnprocessableEntity:
		return models.ErrClassInvalidRequest
	default:
		return models.ErrClassUpstream4xx
	}
}

// contextLengthPhrases are how upstreams word an over-long prompt: OpenAI and
// its compatibles, Anthropic, and Gemini.
var contextLengthPhrases = []string{"context length", "context_length", "context window", "prompt is too long", "too many tokens", "input token count"}

func isContextLength(e *models.APIError) bool {
	if e.Code == "context_length_exceeded" || e.Code == "string_above_max_length" {
		return true
	}
	msg := strings.ToLower(e.Message)
	for _, p := range contextLengthPhrases {
		if strings.Contains(msg, p) {
			return true
		}
	}
	return false
}

func isContentFilter(e *models.APIError) bool {
	switch e.Code {
	case "content_filter", "content_policy_violation", "content_filtered":
		return true
	}
	msg := strings.ToLower(e.Message)
	return strings.Contains(msg, "content filter") || strings.Contains(msg, "content management policy")
}

// AttemptReason is the reason for an attempt made after
// prev failed: "" for a first attempt, and for a failure no reason names.
// After a skipped hop, the reason is the provider error before the skip.
func AttemptReason(prev error) string {
	if _, before, ok := SkippedHop(prev); ok {
		prev = before
	}
	if prev == nil {
		return models.ReasonNone
	}
	class, _ := ClassifyError(prev, 0)
	switch class {
	case models.ErrClassRateLimited:
		return models.ReasonStatus429
	case models.ErrClassUpstream5xx:
		return models.ReasonStatus5xx
	case models.ErrClassTimeout:
		return models.ReasonTimeout
	case models.ErrClassNetwork:
		return models.ReasonNetwork
	case models.ErrClassCircuitOpen:
		return models.ReasonCircuitOpen
	case models.ErrClassContextLength:
		return models.ReasonContextLength
	case models.ErrClassContentFilter:
		return models.ReasonContentFilter
	default:
		return models.ReasonNone
	}
}

// AttemptResult is how an attempt that ended with err went: its outcome,
// error class and origin. ctx is the attempt's context: the provider adapters
// report an ended context as a gateway timeout, so when the attempt's error
// is a timeout or a transport failure and ctx has ended, ctx says whether the
// client cancelled or the gateway's deadline expired.
func AttemptResult(ctx context.Context, err error) (models.AttemptOutcome, models.ErrorClass, models.ErrorOrigin) {
	if err == nil {
		return models.OutcomeSuccess, "", ""
	}
	class, origin := ClassifyError(err, 0)
	if ctxErr := ctx.Err(); ctxErr != nil && (class == models.ErrClassTimeout || class == models.ErrClassNetwork) {
		class, origin = ClassifyError(ctxErr, 0)
	}
	switch {
	case IsHopSkipped(err):
		return models.OutcomeSkipped, class, origin
	case class == models.ErrClassCancelled:
		return models.OutcomeCancelled, class, origin
	case class == models.ErrClassTimeout:
		return models.OutcomeTimeout, class, origin
	case class == models.ErrClassCircuitOpen:
		return models.OutcomeCircuitOpen, class, origin
	default:
		return models.OutcomeError, class, origin
	}
}

// AttemptStatus is the HTTP status a failed attempt's error carries, 0 when
// it carries none. It is the upstream's own status when the adapter kept it
// in the error code (an unparsed 429 or 503 is reported as a 502), so the
// status agrees with the attempt's error class.
func AttemptStatus(err error) int {
	if IsHopSkipped(err) {
		return 0
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		if n, ok := upstreamStatusCode(apiErr); ok && n > 0 {
			return n
		}
		return apiErr.Status
	}
	return 0
}

// credentialPatterns match credentials an upstream may echo back in an error
// message: provider API keys, bearer tokens, cloud access keys and key=value
// pairs, including URL query parameters.
var credentialPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+`),
	regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{8,}`),
	regexp.MustCompile(`\bAIza[0-9A-Za-z_-]{20,}`),
	regexp.MustCompile(`\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`),
	regexp.MustCompile(`(?i)\b((?:x-)?api[_-]?key|key|token|access[_-]?token|secret|password)(["']?\s*[:=]\s*["']?)[^\s"'&,;)}]+`),
}

// AttemptErrorMessage is err's message for an attempt record: credentials
// replaced by [redacted], cut to MaxAttemptErrorMessage characters.
func AttemptErrorMessage(err error) string {
	if err == nil {
		return ""
	}
	msg := err.Error()
	var apiErr *models.APIError
	if !IsHopSkipped(err) && errors.As(err, &apiErr) && apiErr.Message != "" {
		msg = apiErr.Message
	}
	for i, re := range credentialPatterns {
		if i == len(credentialPatterns)-1 {
			msg = re.ReplaceAllString(msg, "${1}${2}[redacted]")
			continue
		}
		msg = re.ReplaceAllString(msg, "[redacted]")
	}
	if utf8.RuneCountInString(msg) > MaxAttemptErrorMessage {
		runes := []rune(msg)
		msg = string(runes[:MaxAttemptErrorMessage])
	}
	return msg
}
