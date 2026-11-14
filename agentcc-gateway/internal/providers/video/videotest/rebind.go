package videotest

import (
	"context"
	"encoding/binary"
	"io"
	"net"
	"sync/atomic"
)

// RebindingResolver answers every A query with first until the returned flag is
// set, then with then. It lets a test pass a host through an earlier lookup and
// have it resolve to a forbidden address at the moment the transport dials,
// without real DNS. AAAA and other queries get an empty answer.
func RebindingResolver(first, then net.IP) (*net.Resolver, *atomic.Bool) {
	rebound := new(atomic.Bool)
	return &net.Resolver{PreferGo: true, Dial: func(context.Context, string, string) (net.Conn, error) {
		client, server := net.Pipe()
		go func() {
			defer server.Close()
			var size [2]byte
			if _, e := io.ReadFull(server, size[:]); e != nil {
				return
			}
			q := make([]byte, binary.BigEndian.Uint16(size[:]))
			if _, e := io.ReadFull(server, q); e != nil {
				return
			}
			end := 12
			for end < len(q) && q[end] != 0 {
				end += int(q[end]) + 1
			}
			end += 5
			if end > len(q) {
				return
			}
			a := append([]byte(nil), q[:end]...)
			a[2], a[3] = 0x81, 0x80 // response, recursion available, no error
			a[6], a[7] = 0, 0       // answer count
			if binary.BigEndian.Uint16(q[end-4:end-2]) == 1 {
				ip := first.To4()
				if rebound.Load() {
					ip = then.To4()
				}
				a[7] = 1
				a = append(a, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
				a = append(a, ip...)
			}
			binary.BigEndian.PutUint16(size[:], uint16(len(a)))
			_, _ = server.Write(append(size[:], a...))
		}()
		return client, nil
	}}, rebound
}
