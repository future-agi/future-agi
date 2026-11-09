// Package wire is the MCP upstream client's HTTP mechanics (plan M13): one
// Streamable HTTP exchange answered in JSON or SSE, a server-sent events
// reader, a size bound on what an upstream sends, the 2026-07-28 mirrored
// headers, the forwarded-header allowlist, _meta merging, list pagination and
// the progress tokens of a shared client. It works on raw JSON-RPC bytes and
// imports nothing of package mcp, which builds the client on it.
package wire

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
)

// ErrTooLarge is an upstream answer or event over its byte limit
// (mcp.max_result_bytes).
var ErrTooLarge = errors.New("upstream answer over mcp.max_result_bytes")

// Event is one server-sent event.
type Event struct {
	ID   string
	Name string // the event field; "" means "message"
	Data []byte
}

// Reader reads server-sent events. Lines have no length limit of their own:
// the caller bounds the stream (Limit) or each event (NewReader's max).
type Reader struct {
	br  *bufio.Reader
	max int64 // the most data bytes one event may carry; <= 0 = no limit
}

// NewReader reads events from r; max bounds one event's data (<= 0: none).
func NewReader(r io.Reader, max int64) *Reader {
	return &Reader{br: bufio.NewReaderSize(r, 32<<10), max: max}
}

// Next returns the next event that has data or a name; comments, retry
// fields and empty events are skipped. io.EOF ends the stream.
func (r *Reader) Next() (Event, error) {
	var ev Event
	var data bytes.Buffer
	lines := 0
	for {
		line, err := r.readLine()
		if err != nil {
			if errors.Is(err, io.EOF) && (lines > 0 || ev.Name != "") {
				ev.Data = data.Bytes()
				return ev, nil // a last event the stream ended without a blank line after
			}
			return Event{}, err
		}
		if len(line) == 0 {
			if lines > 0 || ev.Name != "" {
				ev.Data = data.Bytes()
				return ev, nil
			}
			continue
		}
		field, value, _ := bytes.Cut(line, []byte(":"))
		value = bytes.TrimPrefix(value, []byte(" "))
		switch string(field) {
		case "event":
			ev.Name = string(value)
		case "id":
			ev.ID = string(value)
		case "data":
			if lines > 0 {
				data.WriteByte('\n')
			}
			data.Write(value)
			lines++
			if r.max > 0 && int64(data.Len()) > r.max {
				return Event{}, fmt.Errorf("%w: an event of more than %d bytes", ErrTooLarge, r.max)
			}
		}
	}
}

// readLine reads one line without its line ending (LF, CRLF or a lone CR is
// not split; SSE servers send LF or CRLF).
func (r *Reader) readLine() ([]byte, error) {
	var line []byte
	for {
		frag, err := r.br.ReadSlice('\n')
		line = append(line, frag...)
		if r.max > 0 && int64(len(line)) > r.max+64 {
			return nil, fmt.Errorf("%w: a line of more than %d bytes", ErrTooLarge, r.max)
		}
		if errors.Is(err, bufio.ErrBufferFull) {
			continue
		}
		if err != nil {
			if len(line) > 0 && errors.Is(err, io.EOF) {
				return bytes.TrimRight(line, "\r\n"), nil
			}
			return nil, err
		}
		return bytes.TrimRight(line, "\r\n"), nil
	}
}

// Limit returns a reader of r that fails with ErrTooLarge once more than n
// bytes have been read (n <= 0: no limit).
func Limit(r io.Reader, n int64) io.Reader {
	if n <= 0 {
		return r
	}
	return &limitReader{r: r, left: n}
}

type limitReader struct {
	r    io.Reader
	left int64 // bytes still allowed
}

func (l *limitReader) Read(p []byte) (int, error) {
	if l.left < 0 {
		return 0, ErrTooLarge
	}
	if int64(len(p)) > l.left+1 {
		p = p[:l.left+1] // one byte past the limit is enough to know
	}
	n, err := l.r.Read(p)
	l.left -= int64(n)
	if l.left < 0 {
		return 0, ErrTooLarge
	}
	return n, err
}
