package credtemplate

import (
	"bytes"
	"io"
)

// maxEndpointScan bounds how much of an event stream's start is held to
// find its first event; a longer first event passes through unchanged.
const maxEndpointScan = 64 << 10

// maxEmptyReads is how many reads in a row may return nothing before the
// scan gives up and passes the stream through (io.ErrNoProgress's bound).
const maxEmptyReads = 100

// endpointBody is an event stream's body whose first event, when it is an
// HTTP+SSE "endpoint" event, names the message endpoint in the template's
// form: a server mounted under a secret path, or that echoes a query key,
// sends the endpoint with the secret in it, and the client would keep that
// URL and print it in every error about it. Comments before the first
// event pass at once (a keep-alive is never held back), only the first
// event is held until it is complete, and everything after it passes
// through as it came.
type endpointBody struct {
	body    io.ReadCloser
	t       *Template
	head    []byte // the first event so far, not yet released
	pending []byte // released bytes not yet read
	done    bool   // the first event is settled: the rest passes through
	err     error  // the body's error, returned once pending is read
}

func newEndpointBody(body io.ReadCloser, t *Template) io.ReadCloser {
	return &endpointBody{body: body, t: t}
}

func (b *endpointBody) Read(p []byte) (int, error) {
	buf := make([]byte, 4096)
	for empty := 0; ; {
		if len(b.pending) > 0 {
			n := copy(p, b.pending)
			b.pending = b.pending[n:]
			return n, nil
		}
		if b.done {
			if b.err != nil {
				return 0, b.err
			}
			return b.body.Read(p)
		}
		n, err := b.body.Read(buf)
		if n == 0 && err == nil {
			if empty++; empty >= maxEmptyReads {
				b.err, b.done = io.ErrNoProgress, true
			}
			continue
		}
		empty = 0
		b.head = append(b.head, buf[:n]...)
		ready, rest, done := b.t.scanFirstEvent(b.head, err != nil)
		b.pending, b.head, b.done = append(b.pending, ready...), rest, done
		if err != nil || len(b.head) > maxEndpointScan {
			b.pending, b.head, b.err, b.done = append(b.pending, b.head...), nil, err, true
		}
	}
}

func (b *endpointBody) Close() error { return b.body.Close() }

// scanFirstEvent splits head, the start of an event stream, into what may
// be released now and what is held: complete blocks that dispatch no event
// (comments, an event with no data) are ready at once; the first event
// that dispatches is released whole, with its data in the template's form
// when it is an "endpoint" event, and done is true from then on; an
// incomplete block is held (rest) until more comes or atEnd.
func (t *Template) scanFirstEvent(head []byte, atEnd bool) (ready, rest []byte, done bool) {
	var block [][]byte // the current block's lines, each with its line ending
	blockStart, released := 0, 0
	for pos := 0; pos < len(head); {
		end, next := lineEnd(head, pos, atEnd)
		if end < 0 {
			break // an incomplete line: wait for more
		}
		if end > pos { // a field or comment line
			if block == nil {
				blockStart = pos
			}
			block = append(block, head[pos:next])
			pos = next
			continue
		}
		// A blank line ends the block; end is where that line starts.
		pos = next
		if !hasData(block) {
			block, released = nil, next // dispatches nothing: release it
			continue
		}
		if eventName(block) != "endpoint" {
			return head, nil, true
		}
		var out bytes.Buffer
		out.Write(head[:blockStart])
		for _, l := range block {
			out.Write(t.rewriteDataLine(l))
		}
		out.Write(head[end:])
		return out.Bytes(), nil, true
	}
	if atEnd {
		return head, nil, true
	}
	return head[:released], head[released:], false
}

// lineEnd finds the end of the line at pos (before its CR, LF or CRLF) and
// where the next one starts; -1 when the line is not complete yet.
func lineEnd(head []byte, pos int, atEnd bool) (end, next int) {
	i := bytes.IndexAny(head[pos:], "\r\n")
	if i < 0 {
		return -1, -1
	}
	end = pos + i
	if head[end] == '\r' {
		if end+1 == len(head) && !atEnd {
			return -1, -1 // a CR that may start a CRLF
		}
		if end+1 < len(head) && head[end+1] == '\n' {
			return end, end + 2
		}
	}
	return end, end + 1
}

// field splits an event-stream line into its field name and value (one
// leading space dropped from the value).
func field(line []byte) (name, value []byte) {
	line = bytes.TrimRight(line, "\r\n")
	name, value, found := bytes.Cut(line, []byte(":"))
	if found && len(value) > 0 && value[0] == ' ' {
		value = value[1:]
	}
	return name, value
}

func hasData(block [][]byte) bool {
	for _, l := range block {
		if name, _ := field(l); string(name) == "data" {
			return true
		}
	}
	return false
}

func eventName(block [][]byte) string {
	name := "message"
	for _, l := range block {
		if f, v := field(l); string(f) == "event" {
			name = string(v)
		}
	}
	return name
}

// rewriteDataLine is a data line with its URL in the template's form; any
// other line is returned as it is.
func (t *Template) rewriteDataLine(line []byte) []byte {
	name, value := field(line)
	if string(name) != "data" {
		return line
	}
	ending := line[len(bytes.TrimRight(line, "\r\n")):]
	return append([]byte("data: "+t.untemplate(string(value))), ending...)
}
