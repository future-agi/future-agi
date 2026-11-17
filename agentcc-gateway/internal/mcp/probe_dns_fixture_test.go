package mcp

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"strings"
	"time"
)

// probeDNSResolver is a DNS-only injected resolver for the name-path test.
// net.Resolver consults /etc/hosts even with PreferGo and a custom Dial: leased
// GCE images can pin metadata.google.internal there, bypassing the DNS stub.
// This fixture deliberately has no host-file path. Dial is still real loopback
// UDP; the test asserts both Dial and the DNS server are actually reached.
type probeDNSResolver struct {
	Dial func(context.Context, string, string) (net.Conn, error)
}

func (r *probeDNSResolver) LookupIP(ctx context.Context, network, host string) ([]net.IP, error) {
	if network != "ip4" {
		return nil, fmt.Errorf("DNS fixture requires an A lookup")
	}
	name := strings.TrimSuffix(host, ".")
	query := []byte{0x74, 0x6e, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0}
	for _, label := range strings.Split(name, ".") {
		if len(label) == 0 || len(label) > 63 {
			return nil, fmt.Errorf("invalid DNS fixture label")
		}
		query = append(query, byte(len(label)))
		query = append(query, label...)
	}
	query = append(query, 0, 0, 1, 0, 1) // end of name; IN/A
	conn, err := r.Dial(ctx, "udp4", "")
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	deadline := time.Now().Add(2 * time.Second)
	if end, ok := ctx.Deadline(); ok && end.Before(deadline) {
		deadline = end
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return nil, err
	}
	if n, err := conn.Write(query); err != nil || n != len(query) {
		return nil, fmt.Errorf("loopback DNS write incomplete: %v", err)
	}
	wire := make([]byte, 4096)
	n, err := conn.Read(wire)
	if err != nil {
		return nil, err
	}
	// Pin the ID, flags, question, IN/A answer and address; never accept a
	// truncated answer, a different question or an unverified address.
	want, err := metadataDNSAnswer(query, name)
	if err != nil || !bytes.Equal(wire[:n], want) {
		return nil, fmt.Errorf("unexpected loopback DNS answer")
	}
	return []net.IP{append(net.IP(nil), wire[n-4:n]...)}, nil
}
