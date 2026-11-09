"""J02-ADOPT-01 backend transport controls; isolated process, no Django app boot.

Real AF_UNIX framing/ACK with synthetic mail only. The TypeScript session sink
owns exact allowlist admission; this transport peer does not certify that sink.
Runtime execution is owed; authoring/AST parsing is not a passing test receipt.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from uuid import uuid4


def local_only(event, args):
    if event in ('socket.getaddrinfo', 'socket.sendto'):
        raise RuntimeError('mail backend controls are AF_UNIX only')
    if event in ('socket.__new__', 'socket.connect', 'socket.bind'):
        family = args[1] if event == 'socket.__new__' else args[0].family
        if family != socket.AF_UNIX:
            raise RuntimeError('mail backend controls are AF_UNIX only')


sys.addaudithook(local_only)
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

settings.configure(DEFAULT_CHARSET='utf-8')
spec = importlib.util.spec_from_file_location('j02_local_mail_backend', Path(__file__).resolve().parents[1] / 'lib/local_mail_backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class MailSessionsTransport(unittest.TestCase):
    def setUp(self):
        self.transport_nonce = str(uuid4())
        self.directory = Path(tempfile.gettempdir()).resolve() / f'e2e-mail-{self.transport_nonce}'
        self.directory.mkdir(mode=0o700)
        self.path = str(self.directory / 'mail.sock')
        settings.E2E_MAIL_CAPTURE_SOCKET = self.path
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        os.chmod(self.path, 0o600)
        self.listener.listen(1)
        self.listener.settimeout(2)

    def tearDown(self):
        self.listener.close()
        Path(self.path).unlink()
        self.directory.rmdir()

    def exchange(self, nonce, ack=b'OK\n'):
        recipient = f'e2e-{nonce}-invite@example.invalid'
        href = 'http://127.0.0.1:4173/auth/jwt/invitation/accept/synthetic/synthetic-token'
        message = EmailMultiAlternatives('Synthetic invitation', href, 'e2e-sender@example.invalid', [recipient])
        message.attach_alternative(f'<a href="{href}">Accept</a>', 'text/html')
        received, failures = [], []

        def peer():
            try:
                with self.listener.accept()[0] as client:
                    client.settimeout(2)
                    data = b''
                    while True:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > backend.MAX_MAIL_FRAME + 4:
                            raise ValueError('oversized test frame')
                    size = struct.unpack('!I', data[:4])[0]
                    self.assertEqual(len(data), size + 4)
                    received.append(json.loads(data[4:]))
                    client.sendall(ack)
            except Exception:
                failures.append(True)  # No rendered mail or exception text in output.

        thread = threading.Thread(target=peer)
        thread.start()
        try:
            if ack == b'OK\n':
                self.assertEqual(backend.EmailBackend().send_messages([message]), 1)
            else:
                with self.assertRaisesRegex(ValueError, '^mail capture refused$'):
                    backend.EmailBackend().send_messages([message])
        finally:
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(len(received), 1)
        self.assertTrue(received[0]['to'] == [recipient])
        self.assertTrue(received[0]['text'] == href)
        self.assertEqual(settings.E2E_MAIL_CAPTURE_SOCKET, self.path)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ['mail.sock'])

    def test_fresh_session_nonces_use_one_unchanged_backend_socket(self):
        for _ in range(2):
            nonce = str(uuid4())
            self.assertNotEqual(nonce, self.transport_nonce)
            self.exchange(nonce)

    def test_capture_refusal_is_never_counted_as_delivery(self):
        self.exchange(str(uuid4()), ack=b'NO\n')

    def test_non_synthetic_recipient_refuses_before_connect(self):
        message = EmailMultiAlternatives('Synthetic invitation', 'No link', 'e2e-sender@example.invalid', ['recipient@example.invalid'])
        message.attach_alternative('<p>No link</p>', 'text/html')
        with self.assertRaisesRegex(ValueError, '^mail capture refused$'):
            backend.EmailBackend().send_messages([message])

    def test_network_guard_refuses_non_unix_socket(self):
        with self.assertRaisesRegex(RuntimeError, 'AF_UNIX only'):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)


if __name__ == '__main__':
    unittest.main()
