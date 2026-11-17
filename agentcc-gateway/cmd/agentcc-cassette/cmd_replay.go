package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/replay"
)

func runReplay(ctx context.Context, args []string, stderr io.Writer, logger *log.Logger) int {
	var files stringList
	var addr, addrFile, path string
	fs := flag.NewFlagSet("replay", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.Var(&files, "cassette", "a cassette file to serve (repeatable; all of one kind)")
	fs.StringVar(&addr, "addr", "", "listen address (host:port; port 0 picks one)")
	fs.StringVar(&addrFile, "addr-file", "", "write the bound address here once listening")
	fs.StringVar(&path, "path", "/mcp", "the MCP endpoint, for MCP cassettes")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if len(files) == 0 || addr == "" || fs.NArg() > 0 {
		logger.Print("replay: --cassette and --addr are required")
		return exitUsage
	}
	cs := make([]*cassette.Cassette, 0, len(files))
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err == nil {
			var c *cassette.Cassette
			if c, err = cassette.Load(data); err == nil {
				cs = append(cs, c)
				continue
			}
		}
		logger.Printf("replay: %s: %v", file, err)
		return exitFail
	}
	player, err := replay.New(cs, logger)
	if err != nil {
		logger.Printf("replay: %v", err)
		return exitFail
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		logger.Printf("replay: %v", err)
		return exitFail
	}
	srv := &http.Server{Handler: player.Handler(path), ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	bound := ln.Addr().String()
	if addrFile != "" {
		if err := writeAtomic(addrFile, bound+"\n"); err != nil {
			logger.Printf("replay: --addr-file: %v", err)
			_ = srv.Close()
			return exitFail
		}
	}
	logger.Printf("replaying %d %s cassette(s) on %s", len(cs), cs[0].Kind, bound)
	<-ctx.Done()
	shutdown(srv)
	if misses := player.Misses(); len(misses) > 0 {
		logger.Printf("%d request(s) had no recorded reply:", len(misses))
		for _, m := range misses {
			logger.Printf("  MISS %s", m)
		}
		return exitMisses
	}
	return exitOK
}

// runCheck verifies cassettes on disk: strict format, no credential shape,
// no email that is not a placeholder.
func runCheck(files []string, stdout io.Writer, logger *log.Logger) int {
	if len(files) == 0 {
		logger.Print("check: name at least one cassette")
		return exitUsage
	}
	code := exitOK
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err == nil {
			err = cassette.Check(data)
		}
		if err != nil {
			logger.Printf("check: %s: %v", file, err)
			code = exitFail
			continue
		}
		fmt.Fprintf(stdout, "ok %s\n", file)
	}
	return code
}
