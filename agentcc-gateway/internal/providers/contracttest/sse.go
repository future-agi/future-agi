package contracttest

import (
	"bytes"
	"encoding/json"
	"sort"
	"strings"
	"testing"
)

// Event is one server-sent event.
type Event struct {
	// Name is the "event:" field, empty for data-only streams.
	Name string
	// Data is the "data:" lines joined with "\n".
	Data []byte
}

// Type is the event's type: its SSE name, or for a data-only stream the
// "type" string of its JSON payload, or "" when it has neither (OpenAI chat
// chunks, the "[DONE]" sentinel).
func (e Event) Type() string {
	if e.Name != "" {
		return e.Name
	}
	var probe struct {
		Type string `json:"type"`
	}
	if json.Unmarshal(e.Data, &probe) == nil {
		return probe.Type
	}
	return ""
}

// ParseSSE splits a text/event-stream body into events, per the WHATWG
// server-sent events format: CRLF or LF line endings, ":" comment lines,
// one optional space after the field colon, multi-line data joined with
// "\n", and "id"/"retry" fields ignored. A final event without its trailing
// blank line is still returned. Events with no data are dropped.
//
// It does not handle AWS event-stream (Bedrock's binary framing); that needs
// its own decoder.
func ParseSSE(body []byte) []Event {
	body = bytes.ReplaceAll(body, []byte("\r\n"), []byte("\n"))
	var (
		events  []Event
		name    string
		data    []string
		hasData bool
	)
	dispatch := func() {
		if hasData {
			events = append(events, Event{Name: name, Data: []byte(strings.Join(data, "\n"))})
		}
		name, data, hasData = "", nil, false
	}
	for _, line := range strings.Split(string(body), "\n") {
		line = strings.TrimSuffix(line, "\r")
		if line == "" {
			dispatch()
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		field, value, _ := strings.Cut(line, ":")
		value = strings.TrimPrefix(value, " ")
		switch field {
		case "event":
			name = value
		case "data":
			data = append(data, value)
			hasData = true
		}
	}
	dispatch()
	return events
}

// EncodeSSE is ParseSSE's inverse: "event:" (when named) and one "data:"
// line per line of Data, then a blank line, for each event.
func EncodeSSE(events []Event) []byte {
	var b bytes.Buffer
	for _, e := range events {
		if e.Name != "" {
			b.WriteString("event: " + e.Name + "\n")
		}
		for _, line := range strings.Split(string(e.Data), "\n") {
			b.WriteString("data: " + line + "\n")
		}
		b.WriteString("\n")
	}
	return b.Bytes()
}

// Codec splits a stream body into events and joins events back into a body,
// for the mutation self-test. Decode(Encode(events)) must give the events
// back. A provider whose stream is not SSE (Bedrock's AWS event-stream) sets
// Strict.Codec to its own; Decode must yield one Event per document the
// adapter decodes, with that document's JSON as Data.
type Codec struct {
	Decode func(body []byte) []Event
	Encode func(events []Event) []byte
}

// SSE is the text/event-stream codec.
var SSE = Codec{Decode: ParseSSE, Encode: EncodeSSE}

// AssertKnownEventTypes fails for every distinct event Type() not in known.
// It checks the fixture itself, independently of what the adapter does with
// it, so a recording that brings a new event type fails even if the adapter
// would skip it without a trace.
func AssertKnownEventTypes(t testing.TB, events []Event, known ...string) {
	t.Helper()
	ok := make(map[string]bool, len(known))
	for _, k := range known {
		ok[k] = true
	}
	unknown := map[string]bool{}
	for _, e := range events {
		if typ := e.Type(); !ok[typ] {
			unknown[typ] = true
		}
	}
	if len(unknown) == 0 {
		return
	}
	names := make([]string, 0, len(unknown))
	for n := range unknown {
		names = append(names, n)
	}
	sort.Strings(names)
	t.Errorf("stream has event types outside the provider's known set: %q", names)
}
