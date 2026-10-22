package mock

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

// streamCase is one streamed call. With testConfig, a complete test/model-a
// call is 1,000 prompt tokens x $2/M + 100 completion tokens x $10/M = 3,000
// micros, and its content, "(mock test/model-a) hello", streams as 25 runes
// in pieces of 6, 6, 6 and 7.
type streamCase struct {
	name, path, body string
	cutAfter         int   // events sent before a cut: 12 output runes (8 for the tool call)
	full             int64 // the complete call's charge, in micros
	outRunes         int   // the output runes a complete stream sends
	prompt, output   float64
}

func (tc streamCase) abortAfter() string { return fmt.Sprintf("stream_abort_after:%d", tc.cutAfter) }

func streamCases() []streamCase {
	chat := `{"model":"test/model-a","stream":true,"messages":[{"role":"user","content":"hi"}]}`
	return []streamCase{
		// The role chunk, then content.
		{"chat", "/v1/chat/completions", chat, 3, 3000, 25, 0.000002, 0.00001},
		// message_start and content_block_start, then content.
		{"messages", "/v1/messages", chat, 4, 3000, 25, 0.000002, 0.00001},
		// response.created and output_item.added, then deltas.
		{"responses", "/v1/responses", `{"model":"test/model-a","stream":true,"input":"hi"}`, 4, 3000, 25, 0.000002, 0.00001},
		// The role chunk and the tool-call head, then the first of the two
		// fragments of {"query":"mock"}. mock/tools is uncatalogued, so it is
		// 1,000 x $3/M + 100 x $4/M = 3,400 micros complete.
		{"chat tool call", "/v1/chat/completions", strings.Replace(chat, "test/model-a", "mock/tools", 1), 3, 3400, 16, 0.000003, 0.000004},
	}
}

// An aborted stream is charged only for what it streamed,
// the prompt plus the output tokens of the events sent, priced as a complete
// call is, whether stream_abort_after or the client cut it short. A stream
// that completes is charged in full.
func TestAbortedStreamChargesStreamedOnly(t *testing.T) {
	for _, tc := range streamCases() {
		t.Run(tc.name+"/stream_abort_after", func(t *testing.T) {
			m := newTestMock(t, "")
			events, err := m.streamEvents(t, tc.path, tc.body, tc.abortAfter(), nil)
			if err == nil {
				t.Fatalf("the stream ended cleanly; want %s to drop it", tc.abortAfter())
			}
			assertStreamedCharge(t, m, tc, events)
		})
		t.Run(tc.name+"/client_disconnect", func(t *testing.T) {
			m := newTestMock(t, "")
			read := 0
			events, err := m.streamEvents(t, tc.path, tc.body, "chunk_delay:150", func(string) bool {
				read++
				return read < tc.cutAfter
			})
			if err != nil || len(events) != tc.cutAfter {
				t.Fatalf("read %d events (err %v), want %d before disconnecting", len(events), err, tc.cutAfter)
			}
			assertStreamedCharge(t, m, tc, events)
		})
		t.Run(tc.name+"/completed", func(t *testing.T) {
			m := newTestMock(t, "")
			if _, err := m.streamEvents(t, tc.path, tc.body, "", nil); err != nil {
				t.Fatalf("a complete stream failed: %v", err)
			}
			if got := waitForOneCharge(t, m); got != tc.full {
				t.Fatalf("completed stream charged %d micros, want the complete call's %d", got, tc.full)
			}
		})
	}
}

// The arithmetic of one cut, stated literally so the test does not only
// agree with itself: 12 of 25 runes is 48 of the 100 completion tokens, and
// $0.002 + 48 x $10/M = 2,480 micros.
func TestAbortedStreamChargeArithmetic(t *testing.T) {
	chat := streamCases()[0]
	for _, c := range []struct {
		behavior string
		want     int64
	}{
		{chat.abortAfter(), 2480},
		// usage_cost fixes the complete call's cost; a cut stream pays the
		// share of it that it streamed at table price: $0.01 x 2,480 / 3,000.
		{"usage_cost:0.01,stream_abort_after:3", 8267},
		// Cut after the role chunk, the four content chunks and the finish
		// chunk, before usage and [DONE]: all the output was generated.
		{"stream_abort_after:6", 3000},
		// Cut after the role chunk alone: the prompt only.
		{"stream_abort_after:1", 2000},
	} {
		m := newTestMock(t, "")
		if _, err := m.streamEvents(t, chat.path, chat.body, c.behavior, nil); err == nil {
			t.Fatalf("%s: the stream ended cleanly", c.behavior)
		}
		if got := waitForOneCharge(t, m); got != c.want {
			t.Errorf("%s: charged %d micros, want %d", c.behavior, got, c.want)
		}
	}
}

// A completed stream is charged as soon as its output is out, before its
// closing events, so a client that has read the whole stream always finds
// the charge recorded.
func TestCompletedStreamIsChargedBeforeItCloses(t *testing.T) {
	for _, tc := range streamCases() {
		t.Run(tc.name, func(t *testing.T) {
			m := newTestMock(t, "")
			out, checked := 0, false
			_, err := m.streamEvents(t, tc.path, tc.body, "chunk_delay:300", func(data string) bool {
				if out == tc.outRunes && !checked {
					// The first event after the last output; the closing
					// events are still at least 300 ms away.
					checked = true
					if total, n := m.srv.State().TotalChargeMicros(); n != 1 || total != tc.full {
						t.Errorf("before the stream closed: %d charges, %d micros; want 1 of %d", n, total, tc.full)
					}
				}
				out += clientOutputRunes(t, data)
				return true
			})
			if err != nil || !checked {
				t.Fatalf("stream err %v; reached the end of the output: %v", err, checked)
			}
		})
	}
}

func assertStreamedCharge(t *testing.T, m *testMock, tc streamCase, events []string) {
	t.Helper()
	sent := 0
	for _, data := range events {
		sent += clientOutputRunes(t, data)
	}
	if sent == 0 || sent >= tc.outRunes {
		t.Fatalf("the cut should fall inside the output: %d of %d runes sent", sent, tc.outRunes)
	}
	want := usdToMicros(1000*tc.prompt + float64(100*sent/tc.outRunes)*tc.output)
	if got := waitForOneCharge(t, m); got != want || got >= tc.full {
		t.Fatalf("charged %d micros, want %d (the prompt + %d of %d output runes), below the complete %d",
			got, want, sent, tc.outRunes, tc.full)
	}
}

// clientOutputRunes counts the output text one event's data carries, read
// as a client reads the chat, Anthropic and Responses stream shapes.
func clientOutputRunes(t *testing.T, data string) int {
	t.Helper()
	if data == "[DONE]" {
		return 0
	}
	var ev struct {
		Type    string          `json:"type"`
		Delta   json.RawMessage `json:"delta"`
		Choices []struct {
			Delta struct {
				Content   string `json:"content"`
				ToolCalls []struct {
					Function struct {
						Arguments string `json:"arguments"`
					} `json:"function"`
				} `json:"tool_calls"`
			} `json:"delta"`
		} `json:"choices"`
	}
	if err := json.Unmarshal([]byte(data), &ev); err != nil {
		t.Fatalf("event %q: %v", data, err)
	}
	n := 0
	for _, c := range ev.Choices {
		n += utf8.RuneCountInString(c.Delta.Content)
		for _, call := range c.Delta.ToolCalls {
			n += utf8.RuneCountInString(call.Function.Arguments)
		}
	}
	switch ev.Type {
	case "response.output_text.delta", "response.function_call_arguments.delta":
		var s string
		if err := json.Unmarshal(ev.Delta, &s); err != nil {
			t.Fatalf("%s delta %s: %v", ev.Type, ev.Delta, err)
		}
		n += utf8.RuneCountInString(s)
	case "content_block_delta":
		var d map[string]string
		if err := json.Unmarshal(ev.Delta, &d); err != nil {
			t.Fatalf("content_block_delta %s: %v", ev.Delta, err)
		}
		n += utf8.RuneCountInString(d["text"]) + utf8.RuneCountInString(d["partial_json"])
	}
	return n
}

// streamEvents posts a streamed call and hands each event's data to onEvent
// (nil reads to the end); onEvent returning false disconnects the client. It
// returns the events read, and the read error of a stream that was dropped.
func (m *testMock) streamEvents(t *testing.T, path, body, behavior string, onEvent func(data string) bool) ([]string, error) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", m.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer key-1")
	if behavior != "" {
		req.Header.Set(BehaviorHeader, behavior)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var events []string
	rd := bufio.NewReader(resp.Body)
	for {
		line, err := rd.ReadString('\n')
		if data, ok := strings.CutPrefix(strings.TrimRight(line, "\n"), "data: "); ok {
			events = append(events, data)
			if onEvent != nil && !onEvent(data) {
				return events, nil // the deferred cancel closes the connection
			}
		}
		if err == io.EOF {
			return events, nil
		}
		if err != nil {
			return events, err
		}
	}
}

// waitForOneCharge waits for the call's single charge (a disconnected stream
// is charged once the mock notices the client has gone) and returns it.
func waitForOneCharge(t *testing.T, m *testMock) int64 {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		total, n := m.srv.State().TotalChargeMicros()
		if n == 1 {
			return total
		}
		if n > 1 || time.Now().After(deadline) {
			t.Fatalf("want exactly one charge, have %d (%d micros)", n, total)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
