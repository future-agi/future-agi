"""Verified OCI layouts and private loopback-only CI image transport.

OCIRegistry is the GET-only chart server. DockerHubMirror streams verified PR
image exports to registry:2 on the ephemeral runner, never to an external
registry. The daemon pulls through its mirror under the normal Docker Hub
names to establish genuine RepoDigests. Neither transport certifies a product.
"""
from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import threading
from http.client import HTTPConnection
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# This writer is only for the ephemeral Actions runner, never Docker Hub.
MIRROR_HOST = 'localhost:5000'
REGISTRY_IMAGE = ('registry:2@sha256:'
                  'a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373')

DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


class OCIRegistry:
    def __init__(self):
        self.repositories = {}
        self.server = self.thread = None

    def add(self, repository, tag, directory):
        if not re.fullmatch(r'futureagi/[a-z0-9-]+', repository) or not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+', tag):
            raise ValueError('invalid owned CI image reference')
        if repository in self.repositories:
            raise ValueError('immutable OCI repository already registered')
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError('OCI directory must not be a symlink')
        directory = directory.resolve()
        for path in (directory/'index.json', directory/'blobs', directory/'blobs/sha256'):
            if path.is_symlink():
                raise ValueError('symlink in OCI layout')
        if (directory/'index.json').stat().st_size > 8*1024*1024:
            raise ValueError('OCI index exceeds bound')
        index = json.loads((directory/'index.json').read_bytes())
        if len(index['manifests']) != 1:
            raise ValueError('single native image descriptor required; disable attestations')
        root = index['manifests'][0]
        blobs = {}

        def visit(descriptor, depth=0):
            digest = descriptor['digest']
            if depth > 8 or not DIGEST.fullmatch(digest):
                raise ValueError('invalid OCI descriptor graph')
            path = directory/'blobs/sha256'/digest[7:]
            if path.is_symlink() or not path.is_file() or path.stat().st_size != descriptor['size']:
                raise ValueError('missing/unsafe OCI blob')
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    h.update(chunk)
            if h.hexdigest() != digest[7:]:
                raise ValueError('OCI digest mismatch')
            media = descriptor['mediaType']
            blobs[digest] = (path, media)
            if media.endswith('+json') and path.stat().st_size > 8*1024*1024:
                raise ValueError('OCI document exceeds bound')
            if media.endswith(('image.index.v1+json', 'manifest.list.v2+json')):
                doc = json.loads(path.read_bytes())
                for item in doc['manifests']:
                    visit(item, depth+1)
            elif media.endswith(('image.manifest.v1+json', 'manifest.v2+json')):
                doc = json.loads(path.read_bytes())
                visit(doc['config'], depth+1)
                for item in doc['layers']:
                    visit(item, depth+1)
        visit(root)
        self.repositories[repository] = (tag, root['digest'], blobs)
        return root['digest']

    def lookup(self, path):
        url = urlsplit(path)
        if url.query or url.fragment:
            return None
        match = re.fullmatch(r'/v2/(futureagi/[a-z0-9-]+)/(manifests|blobs)/([^/]+)', url.path)
        if not match or match[1] not in self.repositories:
            return None
        tag, digest, blobs = self.repositories[match[1]]
        ref = digest if match[2] == 'manifests' and match[3] == tag else match[3]
        return (ref, blobs[ref]) if ref in blobs else None

    def __enter__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self.respond(False)

            def do_GET(self):
                self.respond(True)

            def respond(self, body):
                if self.path == '/v2/':
                    self.send_response(200)
                    self.send_header('Docker-Distribution-Api-Version','registry/2.0')
                    self.send_header('Content-Length','0')
                    self.end_headers()
                    return
                found = owner.lookup(self.path)
                if found is None:
                    self.send_error(404)
                    return
                digest, (path, media) = found
                self.send_response(200)
                self.send_header('Docker-Content-Digest',digest)
                self.send_header('Content-Type',media)
                self.send_header('Content-Length',str(path.stat().st_size))
                self.end_headers()
                if body:
                    with path.open('rb') as stream:
                        shutil.copyfileobj(stream,self.wfile,1024*1024)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        self.host = 'localhost:'+str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        if self.server:
            self.server.shutdown()
            self.thread.join(timeout=10)
            self.server.server_close()


class DockerHubMirror:
    """Push unmodified OCI bytes to a fixed local registry; no login or redirect.

    A plain registry is sufficient: Docker tries configured Hub mirrors first,
    then Hub on a mirror miss. Proxy mode would reject uploads. PR tags and the
    version tag required by retained Compose refer to exactly the same manifest.
    """
    host = MIRROR_HOST

    def request(self, method, path, *, body=None, headers=None, statuses=(200,)):
        connection = HTTPConnection(self.host, timeout=300)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            metadata = dict(response.getheaders())
            # Never retain third-party bodies (or follow a Location redirect).
            response.read(65536)
            if response.status not in statuses:
                raise RuntimeError('runner-local registry %s failed (HTTP %d)' % (method, response.status))
            return {key.lower(): value for key, value in metadata.items()}
        finally:
            connection.close()

    def upload_path(self, location, repository, digest):
        url = urlsplit(location)
        # Distribution can return an absolute Location. Do not send image data
        # anywhere but the configured local listener, even on a bad response.
        if (url.scheme not in ('', 'http') or url.netloc not in ('', self.host)
                or url.fragment or not re.fullmatch(
                    r'/v2/'+re.escape(repository)+r'/blobs/uploads/[a-zA-Z0-9-]+', url.path)):
            raise ValueError('unsafe local registry upload location')
        query = parse_qsl(url.query, keep_blank_values=True)
        if any(key != '_state' for key, _value in query):
            raise ValueError('unexpected local registry upload query')
        return urlunsplit(('', '', url.path, urlencode(query+[('digest', digest)]), ''))

    def publish(self, repository, version, pr_tag, directory):
        if not re.fullmatch(r'pr-[0-9a-f]{40}-[0-9]+-[0-9]+', pr_tag):
            raise ValueError('PR transport tag must bind revision, run and attempt')
        verified = OCIRegistry()
        digest = verified.add(repository, version, directory)
        _tag, _root, blobs = verified.repositories[repository]
        base = '/v2/'+repository
        manifests = []
        for pin, (path, media) in blobs.items():
            if media.endswith(('image.index.v1+json', 'manifest.list.v2+json',
                               'image.manifest.v1+json', 'manifest.v2+json')):
                manifests.append((pin, path, media))
                continue
            response = self.request('POST', base+'/blobs/uploads/', headers={'Content-Length': '0'}, statuses=(202,))
            upload = self.upload_path(response.get('location', ''), repository, pin)
            with path.open('rb') as source:
                result = self.request('PUT', upload, body=source, headers={
                    'Content-Type': 'application/octet-stream', 'Content-Length': str(path.stat().st_size)}, statuses=(201,))
            if result.get('docker-content-digest') != pin:
                raise ValueError('local registry blob digest differs from OCI export')
        # OCIRegistry visits parents before children; upload manifests bottom-up.
        for pin, path, media in reversed(manifests):
            refs = (pin, pr_tag, version) if pin == digest else (pin,)
            for ref in refs:
                result = self.request('PUT', base+'/manifests/'+ref, body=path.read_bytes(),
                    headers={'Content-Type': media}, statuses=(201,))
                if result.get('docker-content-digest') != pin:
                    raise ValueError('local registry manifest digest differs from OCI export')
        return digest
