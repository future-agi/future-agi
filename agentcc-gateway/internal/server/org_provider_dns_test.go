package server

import (
	"context"
	"encoding/binary"
	"io"
	"net"
	"testing"
)

// stubProviderDNS uses net.LookupIP's existing resolver seam without changing
// the providers package's URL validation. These tests are not parallel. Every
// DNS exchange stays in a pipe; a nil address makes all names unresolvable.
func stubProviderDNS(t *testing.T, address net.IP) {
	t.Helper()
	previous := net.DefaultResolver
	net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, server string) (net.Conn, error) {
		if address == nil {
			return nil, &net.DNSError{Err: "fixture name does not exist", Name: "no-such-host.invalid", IsNotFound: true}
		}
		client, peer := net.Pipe()
		go func() {
			defer peer.Close()
			var size [2]byte
			if _, err := io.ReadFull(peer, size[:]); err != nil {
				return
			}
			wire := make([]byte, binary.BigEndian.Uint16(size[:]))
			if _, err := io.ReadFull(peer, wire); err != nil {
				return
			}
			// Echo the resolver's single uncompressed question. Ignore its
			// optional EDNS record and answer A with the supplied IPv4;
			// AAAA has no answers. No DNS service or socket is involved.
			if len(wire) < 17 || binary.BigEndian.Uint16(wire[4:6]) != 1 {
				return
			}
			end := 12
			for end < len(wire) && wire[end] != 0 {
				if wire[end] > 63 {
					return
				}
				end += 1 + int(wire[end])
			}
			if end+5 > len(wire) {
				return
			}
			qtype := binary.BigEndian.Uint16(wire[end+1 : end+3])
			wire = wire[:end+5]
			binary.BigEndian.PutUint16(wire[2:4], 0x8180)
			for i := 6; i < 12; i++ {
				wire[i] = 0
			}
			if qtype == 1 {
				wire[7] = 1
				wire = append(wire, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
				wire = append(wire, address.To4()...)
			}
			binary.BigEndian.PutUint16(size[:], uint16(len(wire)))
			_, _ = peer.Write(append(size[:], wire...))
		}()
		return client, nil
	}}
	t.Cleanup(func() { net.DefaultResolver = previous })
}

// An ambient resolver that resolves .invalid must not change the fixture's
// expected error. Loopback is intentional: even the old fixture's red cannot
// connect to an external host, because URL validation refuses it first.
func TestOrgProviderBuildFailureIndependentOfAmbientDNS(t *testing.T) {
	stubProviderDNS(t, net.IPv4(127, 0, 0, 1))
	ips, err := net.LookupIP("no-such-host.invalid")
	if err != nil || len(ips) != 1 || !ips[0].IsLoopback() {
		t.Fatalf("synthetic resolver did not resolve the control name: ips=%v err=%v", ips, err)
	}
	TestOrgProviderBuildFailureIsA502ServerError(t)
}
