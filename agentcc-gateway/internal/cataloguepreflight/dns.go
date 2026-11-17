//go:build cataloguepreflighttest

// Package cataloguepreflight contains owned, test-only fixtures. Nothing in
// this package installs containment or authorizes a catalogue action.
package cataloguepreflight

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"net"
	"strings"
	"sync/atomic"
	"time"
)

// DNS has one exact question and answer, a retained UDP socket, and no fallback
// through net.Resolver, hosts, NSS, search suffixes, or the system DNS server.
type DNS struct {
	Name, Port       string
	IP               net.IP
	conn             net.PacketConn
	queries, lookups atomic.Int64
	failures         atomic.Int64
	done             chan struct{}
}

func DNSQuestion(name string, id uint16) ([]byte, error) {
	if !strings.Contains(name, ".") || strings.HasSuffix(name, ".") || len(name) > 253 {
		return nil, errors.New("DNS fixture requires a dotted absolute registration without trailing dot")
	}
	q := []byte{byte(id >> 8), byte(id), 1, 0, 0, 1, 0, 0, 0, 0, 0, 0}
	for _, label := range strings.Split(name, ".") {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return nil, errors.New("invalid fixture label")
		}
		for _, c := range label {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-') {
				return nil, errors.New("invalid fixture character")
			}
		}
		q = append(q, byte(len(label)))
		q = append(q, label...)
	}
	return append(q, 0, 0, 1, 0, 1), nil
}

func DNSAnswer(query []byte, name string, ip net.IP) ([]byte, error) {
	if len(query) < 12 || ip.To4() == nil {
		return nil, errors.New("invalid DNS fixture packet or answer")
	}
	want, err := DNSQuestion(name, binary.BigEndian.Uint16(query[:2]))
	if err != nil || !bytes.Equal(query, want) {
		return nil, errors.New("unregistered DNS question")
	}
	a := []byte{query[0], query[1], 0x85, 0x80, 0, 1, 0, 1, 0, 0, 0, 0}
	a = append(a, query[12:]...)
	a = append(a, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
	return append(a, ip.To4()...), nil
}

func StartDNS(name, port string, ip net.IP) (*DNS, error) {
	if _, err := DNSQuestion(name, 1); err != nil {
		return nil, err
	}
	if ip.To4() == nil {
		return nil, errors.New("IPv4 fixture answer required")
	}
	conn, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	d := &DNS{Name: name, Port: port, IP: append(net.IP(nil), ip...), conn: conn, done: make(chan struct{})}
	go func() {
		defer close(d.done)
		buf := make([]byte, 4096)
		for {
			n, peer, err := conn.ReadFrom(buf)
			if err != nil {
				return
			}
			a, err := DNSAnswer(buf[:n], name, ip)
			if err != nil {
				d.failures.Add(1)
				continue
			}
			d.queries.Add(1)
			if _, err := conn.WriteTo(a, peer); err != nil {
				d.failures.Add(1)
			}
		}
	}()
	return d, nil
}

func (d *DNS) Address() string { return d.conn.LocalAddr().String() }
func (d *DNS) Close()          { d.conn.Close(); <-d.done }
func (d *DNS) Counts() (int64, int64, int64) {
	return d.lookups.Load(), d.queries.Load(), d.failures.Load()
}

func (d *DNS) Lookup(ctx context.Context, host, port string) ([]net.IPAddr, error) {
	if host != d.Name || port != d.Port {
		return nil, errors.New("unregistered resolver host or port")
	}
	d.lookups.Add(1)
	var id [2]byte
	if _, err := rand.Read(id[:]); err != nil {
		return nil, err
	}
	q, err := DNSQuestion(host, binary.BigEndian.Uint16(id[:]))
	if err != nil {
		return nil, err
	}
	conn, err := (&net.Dialer{}).DialContext(ctx, "udp4", d.Address())
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
	if n, err := conn.Write(q); err != nil || n != len(q) {
		return nil, errors.New("incomplete owned DNS write")
	}
	wire := make([]byte, 4096)
	n, err := conn.Read(wire)
	if err != nil {
		return nil, err
	}
	want, err := DNSAnswer(q, host, d.IP)
	if err != nil || !bytes.Equal(wire[:n], want) {
		return nil, errors.New("unexpected owned DNS answer")
	}
	return []net.IPAddr{{IP: append(net.IP(nil), wire[n-4:n]...)}}, nil
}
