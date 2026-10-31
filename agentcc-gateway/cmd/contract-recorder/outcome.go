package main

import (
	"encoding/json"
	"fmt"
	"maps"
	"mime"
	"net/http"
	"reflect"
	"regexp"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// recording is what one exchange leaves in the case directory.
type recording struct {
	// File is the response file name, Body its contents (redacted).
	File string
	Body []byte
	// Spec is the case.json to write; nil leaves the case's as it is (or,
	// in live mode, lets Discover fall back to the repo's).
	Spec *contracttest.CaseSpec
	// Changes say what the recording changed in case.json; Warnings what a
	// reviewer should look at.
	Changes, Warnings []string
}

// decide checks one exchange against what the case expects and works out
// what to save. An error means nothing is saved:
//
//   - an error case (case.json status 400-599) must get exactly that status;
//     a 2xx case must get a 2xx. So neither can overwrite the other.
//   - the response file keeps the kind the case already has; a case with
//     none (a new case, or a clone) gets the kind its Content-Type implies.
//   - an error case's case.json takes the response's whitelisted headers
//     (savedHeaders); a response.bin or response.txt takes its content type
//     when the media type changed; a 2xx case takes a different 2xx status.
//     Everything else in case.json is kept.
func decide(j job, ex exchange, secrets []string) (recording, error) {
	var rec recording
	body, leaked := redact(ex.Body, secrets)
	if leaked {
		rec.Warnings = append(rec.Warnings, "the response contained a credential; it was replaced with REDACTED")
	}
	if j.isError() {
		var hit bool
		if body, hit = scrubCredentials(body); hit {
			rec.Warnings = append(rec.Warnings, "the error body contained something credential-like; it was replaced with REDACTED")
		}
	}
	want, got := j.wantStatus(), ex.Status
	switch {
	case j.isError() && got != want:
		return rec, fmt.Errorf("upstream answered %d %s, but case.json expects %d (not saved): %s",
			got, ex.ContentType, want, snippet(scrubbed(body)))
	case !j.isError() && (got < 200 || got > 299):
		return rec, fmt.Errorf("upstream answered %d %s (not saved; a 2xx case cannot become an error case): %s",
			got, ex.ContentType, snippet(scrubbed(body)))
	}

	file := j.RespFile
	if file == "" {
		file = fileForContentType(ex.ContentType, body, j.isError())
	}
	switch {
	case j.isError() && contracttest.IsStreamFile(file):
		return rec, fmt.Errorf("upstream answered %d with %s; an error case's body must be %s or %s (not saved)",
			got, ex.ContentType, contracttest.ResponseJSONFile, contracttest.ResponseTextFile)
	case file == contracttest.ResponseJSONFile && !json.Valid(body):
		return rec, fmt.Errorf("upstream answered %d %s but the body is not JSON (not saved): %s",
			got, ex.ContentType, snippet(scrubbed(body)))
	}
	rec.File, rec.Body = file, body
	if j.Stream && !j.isError() && !contracttest.IsStreamFile(file) {
		rec.Warnings = append(rec.Warnings, fmt.Sprintf("the request asks for a stream, but upstream answered %s (saved as %s; Discover refuses a non-stream file for a stream request)",
			ex.ContentType, file))
	}

	spec := j.Spec
	spec.Headers = maps.Clone(spec.Headers)
	if !j.isError() && got != want {
		spec.Status = got
		rec.Changes = append(rec.Changes, fmt.Sprintf("status %d -> %d", want, got))
	}
	effective := spec.ContentType
	if effective == "" {
		effective = contracttest.DefaultContentType(file)
	}
	switch observed := ex.ContentType; {
	case observed == "":
		rec.Warnings = append(rec.Warnings, fmt.Sprintf("upstream sent no Content-Type; %s replays as %s", file, effective))
	case mediaType(observed) == mediaType(effective):
	case file == contracttest.ResponseBinFile || file == contracttest.ResponseTextFile:
		// The file name cannot say what the bytes are, so case.json does.
		spec.ContentType = observed
		rec.Changes = append(rec.Changes, fmt.Sprintf("content_type %q -> %q", effective, observed))
	default:
		rec.Warnings = append(rec.Warnings, fmt.Sprintf("Content-Type is %q, but %s replays as %s", observed, file, effective))
	}
	if j.isError() {
		hdrs, redacted := savedHeaders(ex.Header, j.Spec.Headers, secrets)
		if redacted {
			rec.Warnings = append(rec.Warnings, "a saved response header looked like a credential; its value was replaced with REDACTED")
		}
		if !reflect.DeepEqual(hdrs, spec.Headers) {
			rec.Changes = append(rec.Changes, "headers: "+headerNames(hdrs))
		}
		spec.Headers = hdrs
	}
	if err := spec.Validate(); err != nil {
		return rec, fmt.Errorf("the case.json this recording needs is invalid: %v", err)
	}

	changed := !reflect.DeepEqual(spec, j.Spec)
	var write bool
	switch {
	case j.WriteRequest:
		// A cloned case exists nowhere else: it needs its own case.json
		// whenever its source has one.
		write = j.HasSpec || changed
	case j.Live:
		// Live mode falls back to the repo's case.json when the recording
		// has none; an error case's status and headers are the live ones.
		write = j.isError() || changed
	default:
		write = changed
	}
	if write {
		rec.Spec = &spec
	}
	return rec, nil
}

// fileForContentType picks the response file for a body a case has no file
// for yet, by its Content-Type.
func fileForContentType(contentType string, body []byte, isError bool) string {
	mt := mediaType(contentType)
	switch {
	case mt == "text/event-stream":
		return contracttest.ResponseSSEFile
	case mt == "application/vnd.amazon.eventstream":
		return contracttest.ResponseEventStreamFile
	case mt == "application/json" || strings.HasSuffix(mt, "+json"):
		return contracttest.ResponseJSONFile // decide rejects it if the body is not JSON
	case strings.HasPrefix(mt, "text/"):
		return contracttest.ResponseTextFile
	case mt == "" && json.Valid(body):
		return contracttest.ResponseJSONFile
	case isError:
		return contracttest.ResponseTextFile
	default:
		return contracttest.ResponseBinFile
	}
}

// mediaType is a Content-Type without its parameters, lower-cased.
func mediaType(ct string) string {
	if mt, _, err := mime.ParseMediaType(ct); err == nil {
		return mt
	}
	mt, _, _ := strings.Cut(ct, ";")
	return strings.ToLower(strings.TrimSpace(mt))
}

// keepHeader reports whether a response header is worth saving in an error
// case's case.json: what clients and the gateway's retry logic act on
// (retry hints, rate-limit state, the AWS error type) and the request ID a
// provider's support asks for. Nothing else: response headers also carry
// organisation and project IDs.
func keepHeader(name string) bool {
	n := strings.ToLower(name)
	switch n {
	case "retry-after", "retry-after-ms", "x-amzn-errortype", "x-request-id", "request-id":
		return true
	}
	return strings.HasPrefix(n, "x-ratelimit-") || strings.HasPrefix(n, "anthropic-ratelimit-")
}

// savedHeaders is the whitelisted subset of h (keepHeader), values joined
// with ", ", every configured secret and anything credential-like replaced
// with REDACTED. A header the committed case.json already has keeps that
// spelling; a new one is lower-cased. Nil when nothing qualifies.
func savedHeaders(h http.Header, existing map[string]string, secrets []string) (map[string]string, bool) {
	spelling := map[string]string{}
	for k := range existing {
		spelling[strings.ToLower(k)] = k
	}
	var out map[string]string
	redacted := false
	for name, vals := range h {
		if !keepHeader(name) {
			continue
		}
		v, hit := redact([]byte(strings.Join(vals, ", ")), secrets)
		v, hit2 := scrubCredentials(v)
		redacted = redacted || hit || hit2
		key := strings.ToLower(name)
		if s, ok := spelling[key]; ok {
			key = s
		}
		if out == nil {
			out = map[string]string{}
		}
		out[key] = string(v)
	}
	return out, redacted
}

func headerNames(h map[string]string) string {
	if len(h) == 0 {
		return "none"
	}
	return strings.Join(slices.Sorted(maps.Keys(h)), ", ")
}

// credentialRe matches values that look like credentials whatever key the
// recorder was given: bearer tokens, OpenAI/Anthropic-style "sk-" keys
// (masked ones included), Groq "gsk_" keys, Google API keys and AWS access
// key IDs.
var credentialRe = regexp.MustCompile(`(?i:\bbearer\s+[A-Za-z0-9._~+/=-]{8,})|\bsk-[A-Za-z0-9_*-]{8,}|\bgsk_[A-Za-z0-9]{8,}|\bAIza[0-9A-Za-z_-]{30,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`)

// scrubCredentials replaces credential-like values in b with REDACTED.
func scrubCredentials(b []byte) ([]byte, bool) {
	if !credentialRe.Match(b) {
		return b, false
	}
	return credentialRe.ReplaceAll(b, []byte("REDACTED")), true
}

// scrubbed is scrubCredentials for a message.
func scrubbed(b []byte) []byte {
	b, _ = scrubCredentials(b)
	return b
}

// snippet shortens a body for a one-line error message.
func snippet(b []byte) string {
	const max = 512
	s := strings.Join(strings.Fields(string(b)), " ")
	if len(s) > max {
		s = s[:max] + "..."
	}
	return s
}
