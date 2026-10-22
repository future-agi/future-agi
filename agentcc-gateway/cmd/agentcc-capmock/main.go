// Command agentcc-capmock is the capability mock upstream: vendor-shaped
// fakes for the capability vendors the gateway's capability packages call, with no
// real key and the standard library only.
//
//	agentcc-capmock --addr 127.0.0.1:0 --addr-file <file> [--public-url <url>] [--watch-pid <pid>]
//
// It refuses to start while any variable named like a vendor key is set
// (capmock.VendorKeyVars). --watch-pid makes it exit once that process is
// gone, so a scenario that dies before its trap runs leaves no mock behind.
// The fakes, directives and control API are documented in package capmock.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-capmock/capmock"
)

type options struct {
	addr, addrFile, publicURL string
	watchPID                  int
}

func parseFlags(args []string) (options, error) {
	var o options
	fs := flag.NewFlagSet("agentcc-capmock", flag.ContinueOnError)
	fs.StringVar(&o.addr, "addr", "", "listen address (required; port 0 picks a free one)")
	fs.StringVar(&o.addrFile, "addr-file", "", "write the bound address here once listening")
	fs.StringVar(&o.publicURL, "public-url", "", "the base URL minted URLs use (default http://<bound address>)")
	fs.IntVar(&o.watchPID, "watch-pid", 0, "exit once this process is gone")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if o.addr == "" {
		return o, errors.New("--addr is required")
	}
	return o, nil
}

// checkEnv refuses an environment that holds a variable named like a
// vendor key; the error names the variables, never their values.
func checkEnv(environ []string) error {
	if names := capmock.VendorKeyVars(environ); len(names) > 0 {
		return fmt.Errorf("refusing to start: %s named like a vendor key; the capability mock never needs a real key, so unset %s",
			strings.Join(names, ", "), map[bool]string{true: "it", false: "them"}[len(names) == 1])
	}
	return nil
}

func main() {
	log.SetPrefix("agentcc-capmock: ")
	if err := checkEnv(os.Environ()); err != nil {
		log.Fatal(err)
	}
	o, err := parseFlags(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	srv, err := serve(o)
	if err != nil {
		log.Fatal(err)
	}
	if o.watchPID > 0 {
		go watch(o.watchPID, stop)
	}
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		srv.Close()
	}
}

func serve(o options) (*http.Server, error) {
	ln, err := net.Listen("tcp", o.addr)
	if err != nil {
		return nil, err
	}
	bound := ln.Addr().String()
	base := o.publicURL
	if base == "" {
		base = "http://" + bound
	}
	m := capmock.New(base)
	if o.addrFile != "" {
		tmp := o.addrFile + ".tmp"
		if err := os.WriteFile(tmp, []byte(bound+"\n"), 0o600); err != nil {
			ln.Close()
			return nil, err
		}
		if err := os.Rename(tmp, o.addrFile); err != nil {
			ln.Close()
			return nil, err
		}
	}
	srv := &http.Server{Handler: m.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("serve: %v", err)
		}
	}()
	log.Printf("listening on %s", bound)
	return srv, nil
}

// watch signals stop once pid has exited.
func watch(pid int, stop chan<- os.Signal) {
	for {
		time.Sleep(500 * time.Millisecond)
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			log.Printf("watched process %d is gone; exiting", pid)
			stop <- syscall.SIGTERM
			return
		}
	}
}
