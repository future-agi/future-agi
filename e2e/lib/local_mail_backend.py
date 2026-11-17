"""Fixture-only rendered-mail transport; mounted/imported by qualified Stage A.

EMAIL_BACKEND = 'local_mail_backend.EmailBackend'
E2E_MAIL_CAPTURE_SOCKET is a fixture-only Django setting, not a product env API.
No fallback, content output, disk mail, SMTP, or background dispatcher.
"""
import json
import os
import re
import socket
import stat
import struct

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

MAX_MAIL_FRAME = 256 * 1024
NONCE = r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}'


def owned_socket():
    path = getattr(settings, 'E2E_MAIL_CAPTURE_SOCKET', '')
    directory = os.path.dirname(path)
    match = re.fullmatch(r'e2e-mail-(' + NONCE + r')', os.path.basename(directory))
    if (not match or not os.path.isabs(path) or os.path.basename(path) != 'mail.sock'
            or os.path.realpath(directory) != directory
            or path != os.path.join(directory, 'mail.sock') or len(os.fsencode(path)) > 103):
        raise ValueError('mail capture refused')
    parent, endpoint = os.lstat(directory), os.lstat(path)
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid() or not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != os.getuid() or stat.S_IMODE(endpoint.st_mode) != 0o600):
        raise ValueError('mail capture refused')
    return path, match.group(1)


class EmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        # Deliberately ignore fail_silently: transport failure is a fixture failure.
        try:
            path, _transport_nonce = owned_socket()
            count = 0
            for message in email_messages:
                # Directory nonce owns the fixed transport, not the mail identity.
                # The active memory-only session admits its EXACT recipient list;
                # no OK ACK means no delivery. No Django settings change per case.
                if (len(message.to) != 1 or message.cc or message.bcc or message.attachments
                        or not re.fullmatch(r'e2e-' + NONCE + r'-[a-z0-9-]+@example\.invalid', message.to[0])):
                    raise ValueError('mail capture refused')
                alternatives = list(getattr(message, 'alternatives', []))
                if any(mimetype != 'text/html' for _, mimetype in alternatives) or len(alternatives) != 1:
                    raise ValueError('mail capture refused')
                # Actual rendered EmailMultiAlternatives parts, never a minted link.
                payload = json.dumps({
                    'to': message.to, 'cc': message.cc, 'bcc': message.bcc,
                    'from': message.from_email, 'replyTo': message.reply_to,
                    'subject': message.subject, 'text': message.body, 'html': alternatives[0][0],
                }, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
                if not 0 < len(payload) <= MAX_MAIL_FRAME:
                    raise ValueError('mail capture refused')
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(3)
                    client.connect(path)
                    client.sendall(struct.pack('!I', len(payload)) + payload)
                    client.shutdown(socket.SHUT_WR)
                    ack = b''
                    while len(ack) <= 3:
                        part = client.recv(4 - len(ack))
                        if not part:
                            break
                        ack += part
                    if ack != b'OK\n':
                        raise ValueError('mail capture refused')
                count += 1
            return count
        except Exception:
            # Never expose rendered bodies, recipient, path or exception arguments.
            raise ValueError('mail capture refused') from None
