package record

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

// maxAnswer bounds one answer from the server being recorded.
const maxAnswer = 16 << 20

var clientInfo = json.RawMessage(`{"name":"agentcc-cassette","version":"1"}`)

// fatalCodes are protocol failures of the recorder's own requests (2026
// header and capability mismatches, an unknown session): a recording that
// meets one is wrong, so it stops rather than record it as vendor data.
var fatalCodes = map[int]bool{-32020: true, -32021: true, -32022: true, -32001: true}

// rpcClient speaks MCP JSON-RPC over Streamable HTTP for the recorder: a
// legacy session (initialize, Mcp-Session-Id, MCP-Protocol-Version) or the
// stateless 2026-07-28 era (_meta and mirrored headers on every request).
type rpcClient struct {
	url       string
	hc        *http.Client
	creds     http.Header
	timeout   time.Duration
	version   string
	stateless bool
	session   string
	nextID    int64
	schemas   map[string]json.RawMessage // tool name -> inputSchema, for Mcp-Param headers
}

// reply is one answer: a result or a JSON-RPC error, and the HTTP status
// when it was not 200.
type reply struct {
	result, rpcErr json.RawMessage
	status         int
}

type rpcMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   json.RawMessage `json:"error,omitempty"`
}

// call sends one request and waits for its answer. A transport failure, an
// HTTP error without a JSON-RPC answer, a rejected credential or a fatal
// protocol code is an error; any other JSON-RPC error is a reply to record.
func (c *rpcClient) call(ctx context.Context, method string, params json.RawMessage) (reply, error) {
	c.nextID++
	id := strconv.FormatInt(c.nextID, 10)
	send := params
	if c.stateless {
		var err error
		if send, err = wire.SetMeta(params, wire.RequestMeta(clientInfo)); err != nil {
			return reply{}, err
		}
	}
	body, err := json.Marshal(rpcMessage{JSONRPC: "2.0", ID: json.RawMessage(id), Method: method, Params: send})
	if err != nil {
		return reply{}, err
	}
	resp, err := c.post(ctx, method, send, body)
	if err != nil {
		return reply{}, err
	}
	defer resp.Body.Close()
	if method == "initialize" {
		c.session = resp.Header.Get("Mcp-Session-Id")
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return reply{}, fmt.Errorf("%s: the server refused the credential (HTTP %d)", method, resp.StatusCode)
	}
	msg, err := readAnswer(resp, id)
	if err != nil {
		return reply{}, fmt.Errorf("%s: HTTP %d: %w", method, resp.StatusCode, err)
	}
	r := reply{result: msg.Result, rpcErr: msg.Error}
	if resp.StatusCode != http.StatusOK {
		r.status = resp.StatusCode
	}
	if code := errorCode(msg.Error); fatalCodes[code] {
		return reply{}, fmt.Errorf("%s: protocol error %d from the server", method, code)
	}
	return r, nil
}

func (c *rpcClient) post(ctx context.Context, method string, params, body []byte) (*http.Response, error) {
	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url, bytes.NewReader(body))
	if err != nil {
		cancel()
		return nil, err
	}
	c.headers(req.Header)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	switch {
	case c.stateless:
		var name struct {
			Name string `json:"name"`
		}
		_ = json.Unmarshal(params, &name)
		for k, vs := range wire.Mirrored(method, params, c.schemas[name.Name], nil) {
			req.Header[k] = vs
		}
		req.Header.Set("MCP-Protocol-Version", wire.Version2026)
	case method != "initialize" && c.version != "":
		req.Header.Set("MCP-Protocol-Version", c.version)
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		cancel()
		return nil, plainError(err)
	}
	resp.Body = &cancelOnClose{ReadCloser: resp.Body, cancel: cancel}
	return resp, nil
}

func (c *rpcClient) headers(h http.Header) {
	for k, vs := range c.creds {
		h[k] = append([]string(nil), vs...)
	}
	if c.session != "" {
		h.Set("Mcp-Session-Id", c.session)
	}
}

// notify sends a notification; the server answers 202 with no body.
func (c *rpcClient) notify(ctx context.Context, method string) error {
	body, _ := json.Marshal(rpcMessage{JSONRPC: "2.0", Method: method})
	resp, err := c.post(ctx, method, nil, body)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("%s: HTTP %d", method, resp.StatusCode)
	}
	return nil
}

// end deletes a legacy session, best effort: a server may not support it.
func (c *rpcClient) end(ctx context.Context) {
	if c.session == "" {
		return
	}
	ctx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, c.url, nil)
	if err != nil {
		return
	}
	c.headers(req.Header)
	if resp, err := c.hc.Do(req); err == nil {
		resp.Body.Close()
	}
}

// readAnswer reads the JSON-RPC answer with the request's id, from a JSON
// body or from an SSE stream (skipping notifications and server requests).
func readAnswer(resp *http.Response, id string) (rpcMessage, error) {
	mt, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if mt != "text/event-stream" {
		var msg rpcMessage
		if err := json.NewDecoder(io.LimitReader(resp.Body, maxAnswer)).Decode(&msg); err != nil {
			return msg, errors.New("the answer is not a JSON-RPC message")
		}
		return msg, answers(msg, id)
	}
	events := wire.NewReader(io.LimitReader(resp.Body, maxAnswer), maxAnswer)
	for {
		ev, err := events.Next()
		if err != nil {
			return rpcMessage{}, fmt.Errorf("the stream ended before the answer: %w", err)
		}
		var msg rpcMessage
		if (ev.Name == "" || ev.Name == "message") && json.Unmarshal(ev.Data, &msg) == nil && answers(msg, id) == nil {
			return msg, nil
		}
	}
}

// answers checks msg is the answer to request id.
func answers(msg rpcMessage, id string) error {
	if msg.JSONRPC != "2.0" || string(bytes.TrimSpace(msg.ID)) != id || (len(msg.Result) == 0) == (len(msg.Error) == 0) {
		return errors.New("the answer is not the response to the request")
	}
	return nil
}

func errorCode(rpcErr json.RawMessage) int {
	var e struct {
		Code int `json:"code"`
	}
	if len(rpcErr) == 0 || json.Unmarshal(rpcErr, &e) != nil {
		return 0
	}
	return e.Code
}

// plainError drops the URL a transport error carries: a recording URL may
// hold a secret (a URL-variable credential), and errors reach logs.
func plainError(err error) error {
	var ue *url.Error
	if errors.As(err, &ue) {
		return fmt.Errorf("%s to the upstream failed: %w", ue.Op, ue.Err)
	}
	return err
}

// cancelOnClose ends a request's timeout context once its body is read.
type cancelOnClose struct {
	io.ReadCloser
	cancel context.CancelFunc
}

func (c *cancelOnClose) Close() error {
	err := c.ReadCloser.Close()
	c.cancel()
	return err
}
