// Command stdiochild is 63_stdio.sh's stand-in stdio MCP server, used when
// the tree has no cmd/agentcc-mcpmock. It speaks newline-delimited JSON-RPC on
// stdin and stdout, with the stdio extras the scenario needs:
//
//	--mode stdio      accepted for agentcc-mcpmock's command line; the only mode
//	--marker <path>   written when stdin closes (the graceful shutdown)
//	--ignore-sigterm  keep running after SIGTERM
//
// Its one tool, env_names, returns the sorted names of the environment
// variables it sees, as a JSON array in a text part (never their values).
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"sort"
	"strings"
	"syscall"
)

type message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  interface{}     `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func main() {
	mode := flag.String("mode", "stdio", "transport mode (stdio only)")
	marker := flag.String("marker", "", "file written when stdin closes")
	ignoreSigterm := flag.Bool("ignore-sigterm", false, "keep running after SIGTERM")
	flag.Parse()
	if *mode != "stdio" {
		fmt.Fprintf(os.Stderr, "stdiochild: --mode %q is not supported\n", *mode)
		os.Exit(2)
	}
	if *ignoreSigterm {
		signal.Ignore(syscall.SIGTERM)
	}

	out := json.NewEncoder(os.Stdout)
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for in.Scan() {
		var req message
		if err := json.Unmarshal(in.Bytes(), &req); err != nil || req.ID == nil {
			continue // not JSON, or a notification
		}
		resp := message{JSONRPC: "2.0", ID: req.ID}
		resp.Result, resp.Error = answer(req)
		if err := out.Encode(resp); err != nil {
			os.Exit(1)
		}
	}

	if *marker != "" {
		if err := os.WriteFile(*marker, []byte("graceful stdin close\n"), 0o600); err != nil {
			fmt.Fprintf(os.Stderr, "stdiochild: marker: %v\n", err)
			os.Exit(1)
		}
	}
}

func answer(req message) (interface{}, *rpcError) {
	switch req.Method {
	case "initialize":
		return map[string]interface{}{
			"protocolVersion": "2025-11-25",
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{}},
			"serverInfo":      map[string]string{"name": "stdiochild", "version": "1"},
		}, nil
	case "ping":
		return map[string]interface{}{}, nil
	case "tools/list":
		return map[string]interface{}{"tools": []map[string]interface{}{{
			"name":        "env_names",
			"description": "The names of the environment variables this server sees.",
			"inputSchema": map[string]interface{}{"type": "object"},
		}}}, nil
	case "tools/call":
		var p struct {
			Name string `json:"name"`
		}
		_ = json.Unmarshal(req.Params, &p)
		if p.Name != "env_names" {
			return nil, &rpcError{Code: -32602, Message: "unknown tool " + p.Name}
		}
		names := []string{}
		for _, kv := range os.Environ() {
			names = append(names, strings.SplitN(kv, "=", 2)[0])
		}
		sort.Strings(names)
		text, _ := json.Marshal(names)
		return map[string]interface{}{"content": []map[string]string{{"type": "text", "text": string(text)}}}, nil
	}
	return nil, &rpcError{Code: -32601, Message: "method not found: " + req.Method}
}
