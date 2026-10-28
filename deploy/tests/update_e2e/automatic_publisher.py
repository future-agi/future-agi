"""Small local HTTPS release publisher for the one-opt-in acceptance fixture.

No Docker is started here. Product qualification invokes OUR fixed rehearsal
producer, which requires real product images and the fixed updater/backup
interfaces. Synthetic transport fixtures are a separate explicit lane; a local
signature establishes only trust, never compatibility. The parent harness must
supply real frozen candidates/checkouts in the product lane, not fakeapp.

with AutomaticPublisher(new_private_dir, lane='product', candidates=..., ...)
exposes source/public_key/ca_file/publish(version)/seed_and_check(checkout).
source is the stable HTTPS origin (both API and asset origins). Prepare every
release through the fixed qualify/package CLI BEFORE installing the real stack;
then publish changes visibility only. Configure the CA/source/key ONCE during
setup. No per-release host files are touched.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import os
import secrets
import shutil
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from deploy.update.compatibility import PROOF_ASSET, decode_document, validate_release_proof
from deploy.update.discovery import ReleaseDiscovery
from deploy.update.httpcache import HttpCache
from deploy.update.manifest import BUNDLE_ASSET, MANIFEST_ASSET, canonical_json, parse_version, validate_manifest
from deploy.update.settings import load_settings
from deploy.update.trust import http_get
from scripts import build_update_compatibility_proof as packager
from scripts import rehearse_update_compatibility as rehearsal

REPOSITORY = rehearsal.REPOSITORY


class AutomaticPublisher:
    def __init__(self, directory: Path, *, lane: str, versions, retained_layout,
                 candidates=None, synthetic_fixtures=None, cosign='cosign', openssl='openssl',
                 docker_context='colima-fagi-update', platform='linux/amd64', run_id=42,
                 deployment_dirs=None, product_factory=None):
        rehearsal.need(lane in ('product','synthetic'), 'explicit publisher lane required')
        rehearsal.need(lane != 'product' or synthetic_fixtures is None, 'synthetic evidence is not product qualification')
        rehearsal.need(lane != 'synthetic' or candidates is None, 'synthetic lane accepts only labeled fixtures')
        self.directory, self.lane = Path(directory), lane
        self.versions, self.retained_layout = tuple(versions), retained_layout
        rehearsal.need(self.versions and self.versions[0] == retained_layout and len(set(self.versions)) == len(self.versions),
                       'invalid local release sequence')
        self.candidates = copy.deepcopy(candidates or {})
        self.deployment_dirs = {v: Path(p).resolve() for v, p in (deployment_dirs or {}).items()}
        self.product_factory = product_factory
        self.fixtures = copy.deepcopy(synthetic_fixtures or {})
        self.cosign = shutil.which(str(cosign)) or str(cosign)
        self.openssl, self.context, self.run_id = str(openssl), docker_context, run_id
        rehearsal.need(platform in rehearsal.SUPPORTED_PLATFORMS,'unreviewed publisher platform')
        self.platform = platform
        self._assets, self._records, self._published, self._prepared = {}, {}, {}, {}
        self._lock = threading.Lock()
        self._server = self._thread = None
        self.requests = []
        self.public_key = self.directory / 'key.pub'
        self.ca_file = self.directory / 'ca.pem'
        self._password = secrets.token_hex(32)

    def _command(self, args, *, timeout=60):
        # package invokes the fixed host cosign by name in its child process.
        env = dict(os.environ, COSIGN_PASSWORD=self._password,
                   PATH=str(Path(self.cosign).parent) + os.pathsep + os.environ.get('PATH', os.defpath))
        return rehearsal.command(args, cwd=rehearsal.ROOT, env=env, timeout=timeout)

    def __enter__(self):
        rehearsal.need(not self.directory.exists(), 'publisher requires a new private directory')
        self.directory.mkdir(mode=0o700, parents=True)
        try:
            self._command([self.cosign,'generate-key-pair','--output-key-prefix',self.directory/'key'])
            ca_key, leaf_key = self.directory/'ca.key', self.directory/'tls.key'
            self._command([self.openssl,'req','-x509','-newkey','rsa:2048','-nodes','-sha256','-days','2',
                           '-subj','/CN=Private updater test CA','-keyout',ca_key,'-out',self.ca_file])
            csr, certificate = self.directory/'tls.csr', self.directory/'tls.pem'
            self._command([self.openssl,'req','-new','-newkey','rsa:2048','-nodes','-sha256',
                           '-subj','/CN=localhost','-keyout',leaf_key,'-out',csr])
            extension = self.directory/'tls.ext'
            extension.write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\n'
                                 'basicConstraints=critical,CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                                 'extendedKeyUsage=serverAuth\n')
            self._command([self.openssl,'x509','-req','-in',csr,'-CA',self.ca_file,'-CAkey',ca_key,
                           '-CAcreateserial','-out',certificate,'-days','2','-sha256','-extfile',extension])
            for path in self.directory.iterdir():
                if path.is_file():
                    path.chmod(0o600)
            publisher = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = 'HTTP/1.1'

                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    with publisher._lock:
                        publisher.requests.append(self.path)
                        raw = publisher.response(self.path)
                    if raw is None:
                        self.send_response(404); self.send_header('Content-Length','0'); self.end_headers()
                        return
                    etag = '"'+hashlib.sha256(raw).hexdigest()+'"'
                    if self.headers.get('If-None-Match') == etag:
                        self.send_response(304); self.send_header('ETag',etag); self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(raw)))
                    self.send_header('ETag',etag)
                    self.end_headers(); self.wfile.write(raw)

            self._server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
            self._server.daemon_threads = True
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(certificate),str(leaf_key))
            self._server.socket = context.wrap_socket(self._server.socket,server_side=True)
            self.source = 'https://127.0.0.1:'+str(self._server.server_port)
            self.api_origin = self.asset_origin = self.source
            self._thread = threading.Thread(target=self._server.serve_forever,daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def __exit__(self, *_args):
        if self._server:
            if self._thread:
                self._server.shutdown(); self._thread.join(timeout=10)
            self._server.server_close()
        self._server = self._thread = None

    def response(self, path):
        url = urlsplit(path)
        base = '/repos/'+REPOSITORY+'/releases'
        if url.path == base:
            query = parse_qs(url.query)
            if set(query) != {'per_page','page'} or query['per_page'] != ['100']:
                return None
            try:
                page = int(query['page'][0])
            except (ValueError,IndexError):
                return None
            if page < 1:
                return None
            records = sorted(self._records.values(),key=lambda r:parse_version(r['tag_name']),reverse=True)
            return canonical_json(records[(page-1)*100:page*100])
        if url.query:
            return None
        if url.path.startswith(base+'/tags/'):
            record = self._records.get(url.path[len(base+'/tags/'):])
            return canonical_json(record) if record else None
        latest = '/'+REPOSITORY+'/releases/latest/download/'
        if url.path.startswith(latest):
            tags = [tag for tag in self._records if '-' not in tag]
            if not tags:
                return None
            tag = max(tags,key=parse_version)
            return self._assets.get('/'+REPOSITORY+'/releases/download/'+tag+'/'+url.path[len(latest):])
        return self._assets.get(url.path)

    def discovery(self, cache):
        from deploy.update.trust import Verifier
        settings = load_settings(self.directory, overrides={
            'FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY':str(self.public_key),
            'FUTURE_AGI_UPDATE_COSIGN_OFFLINE':'true'}, environ={})
        verifier = Verifier(settings,host_only=True,which=lambda name: self.cosign if name=='cosign' else None)
        context = ssl.create_default_context(cafile=str(self.ca_file))
        transport = functools.partial(http_get,ssl_context=context)
        return ReleaseDiscovery(HttpCache(cache,transport=transport),verifier,api_origin=self.source,
                                asset_origin=self.source,source_identity=self.source)

    def prepare(self, version):
        """Qualify before starting acceptance, without exposing the release.

        Qualification owns a disposable product stack. Never run that stack beside
        the enrolled installation. Once enrolled, publish() only changes HTTPS
        visibility; it cannot sign, qualify, or change any installation file.
        """
        rehearsal.need(self._server is not None and version in self.versions,'unknown local release')
        if version in self._prepared:
            expected = self.fixtures.get(version) if self.lane=='synthetic' else self.candidates.get(version)
            rehearsal.need(self._prepared[version]['inputs'] == canonical_json(expected),'immutable local release changed')
            return
        inputs = self.fixtures.get(version) if self.lane=='synthetic' else self.candidates.get(version)
        rehearsal.need(inputs is not None,'frozen candidate missing')
        output = self.directory / version
        output.mkdir(mode=0o700)
        proof = None
        if self.lane == 'synthetic':
            rehearsal.need(set(inputs)=={'manifest','proof','purpose'} and inputs['purpose']=='SYNTHETIC-TRANSPORT-ONLY',
                           'synthetic fixture must be labeled')
            manifest = validate_manifest(inputs['manifest'])
            proof = canonical_json(inputs['proof']) if inputs['proof'] is not None else None
        else:
            manifest = validate_manifest(inputs)
            position = self.versions.index(version)
            rehearsal.need(all(v in self._prepared for v in self.versions[:position]),
                           'prepare releases in predecessor order')
            if position:
                from scripts.update_rehearsal_shipping import copy_retained_files
                previous = self._prepared[self.versions[position-1]]
                retained = output/'retained-deployment'; retained.mkdir(mode=0o700)
                source_dir = self.deployment_dirs.get(self.retained_layout)
                rehearsal.need(source_dir is not None,'retained A deployment directory required')
                execution = self._prepared[self.retained_layout]['manifest']['deployment']
                copy_retained_files(source_dir, retained, execution)
                (retained/'source-manifest.json').write_bytes(previous['assets'][MANIFEST_ASSET])
                (retained/'source-manifest.sigstore.json').write_bytes(previous['assets'][BUNDLE_ASSET])
                if PROOF_ASSET in previous['assets']:
                    (retained/'source-compatibility.json').write_bytes(previous['assets'][PROOF_ASSET])
                target = retained/'target-manifest.json'; target.write_bytes(canonical_json(manifest))
                source_component = previous['manifest']['components']['standalone']
                target_component = manifest['components']['standalone']
                script = rehearsal.ROOT/'scripts/rehearse_update_compatibility.py'
                qualified = output/'qualification'
                self._command([sys.executable,'-I',script,'qualify',
                    '--source-ref',source_component['image']+'@'+source_component['digest'],
                    '--target-ref',target_component['image']+'@'+target_component['digest'],
                    '--platform',self.platform,'--deployment-dir',retained,
                    '--reference-scale','registered-100k','--boot-timeout','4200',
                    '--docker-context',self.context,'--public-key',self.public_key,
                    '--run-id',str(self.run_id),'--out',qualified], timeout=19800)
                # A refusal is a hard error, never a synthetic/notify-only success.
                packaged = output/'package'
                self._command([sys.executable,'-I',script,'package','--rows',qualified,
                    '--target',target,'--public-key',self.public_key,'--key',self.directory/'key.key',
                    '--out',packaged,'--run-id',str(self.run_id)], timeout=300)
                proof = (packaged/PROOF_ASSET).read_bytes()
                manifest = decode_document((packaged/MANIFEST_ASSET).read_bytes())
            else:
                rehearsal.need(manifest.get('compatibility',{}).get('automatic_apply') is not True,
                               'initial release cannot self-certify')
        rehearsal.need(manifest['version']==version,'candidate version mismatch')
        if manifest.get('compatibility',{}).get('validation') is not None:
            rehearsal.need(proof is not None,'required proof missing')
            validate_release_proof(proof,manifest,repository=REPOSITORY)
        else:
            rehearsal.need(proof is None,'uncertified candidate cannot carry proof')
        path, bundle = output/MANIFEST_ASSET, output/BUNDLE_ASSET
        packager.write_pair(((path,canonical_json(manifest)),))
        self._command([self.cosign,'sign-blob','--yes','--key',self.directory/'key.key',
                       '--tlog-upload=false','--use-signing-config=false','--bundle',bundle,path])
        # Use the host's existing explicit local-key trust implementation, not
        # a permissive TLS mode or a fixture replacement of verification.
        from deploy.update.trust import FetchedManifest
        verifier = self.discovery(output/'verification-cache').verifier
        verification = verifier.verify(FetchedManifest(self.source,output,path,path.read_bytes(),manifest,bundle))
        rehearsal.need(verification.verified,'local host signature verification failed')
        assets = ({PROOF_ASSET:proof} if proof is not None else {})
        assets.update({BUNDLE_ASSET:bundle.read_bytes(),MANIFEST_ASSET:path.read_bytes()})
        self._prepared[version] = dict(inputs=canonical_json(inputs), manifest=manifest, assets=assets)

    def publish(self, version):
        rehearsal.need(self._server is not None and version in self.versions,'unknown local release')
        if self.lane == 'synthetic':
            self.prepare(version)
        rehearsal.need(version in self._prepared,'product releases must qualify before acceptance starts')
        prepared = self._prepared[version]
        expected = self.fixtures.get(version) if self.lane=='synthetic' else self.candidates.get(version)
        rehearsal.need(prepared['inputs'] == canonical_json(expected),'immutable local release changed')
        with self._lock:
            if version in self._published:
                return
            assets = prepared['assets']
            prefix = '/'+REPOSITORY+'/releases/download/'+version+'/'
            rehearsal.need(not any(prefix+name in self._assets for name in assets),'release assets already exist')
            self._assets.update({prefix+name:raw for name,raw in assets.items()})
            self._records[version] = {'tag_name':version,'draft':False,'prerelease':'-' in version,
                'assets':[{'name':name} for name in assets]}
            self._published[version] = prepared['inputs']

    def new_checkout(self, name):
        rehearsal.need(self.lane=='product' and self.product_factory is not None,'real ProductHarness required')
        rehearsal.need(all(v in self._prepared for v in self.versions),'qualify all releases before installing A')
        return self.product_factory(self, name)

    def seed_and_check(self, checkout):
        rehearsal.need(self.lane=='product','synthetic transport is not product data qualification')
        from deploy.tests.update_e2e.real_automatic import ProductHarness
        rehearsal.need(isinstance(checkout, ProductHarness),'real ProductHarness required')
        return checkout.seed_and_check()
