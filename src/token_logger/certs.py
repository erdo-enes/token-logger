from __future__ import annotations

import ipaddress
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class CertificateAuthority:
    def __init__(self, confdir: str):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self._x509 = x509
        self._hashes = hashes
        self._serialization = serialization
        self._ec = ec
        self._NameOID = NameOID
        self.confdir = Path(confdir)
        self.confdir.mkdir(parents=True, exist_ok=True)
        self.ca_key_path = self.confdir / "ca-key.pem"
        self.ca_cert_path = self.confdir / "ca.pem"
        self._certs_dir = self.confdir / "leaf-certs"
        self._certs_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._contexts: dict[str, ssl.SSLContext] = {}
        self._load_or_create_ca()

    def _load_or_create_ca(self) -> None:
        from cryptography.hazmat.primitives import serialization

        if self.ca_key_path.exists() and self.ca_cert_path.exists():
            self._ca_key = serialization.load_pem_private_key(self.ca_key_path.read_bytes(), password=None)
            self._ca_cert = self._x509.load_pem_x509_certificate(self.ca_cert_path.read_bytes())
            return
        self._ca_key = self._ec.generate_private_key(self._ec.SECP256R1())
        subject = issuer = self._x509.Name([self._x509.NameAttribute(self._NameOID.COMMON_NAME, "Token Logger CA")])
        now = datetime.now(timezone.utc)
        self._ca_cert = (
            self._x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self._ca_key.public_key())
            .serial_number(self._x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(self._x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(self._x509.SubjectKeyIdentifier.from_public_key(self._ca_key.public_key()), critical=False)
            .sign(self._ca_key, self._hashes.SHA256())
        )
        self.ca_key_path.write_bytes(self._ca_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.ca_cert_path.write_bytes(self._ca_cert.public_bytes(serialization.Encoding.PEM))

    @property
    def ca_pem(self) -> str:
        return str(self.ca_cert_path)

    def server_context(self, host: str) -> ssl.SSLContext:
        host = host.lower()
        with self._lock:
            if host in self._contexts:
                return self._contexts[host]
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization

            key = self._ec.generate_private_key(self._ec.SECP256R1())
            try:
                ip = ipaddress.ip_address(host)
                san = x509.IPAddress(ip)
            except ValueError:
                san = x509.DNSName(host)
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(self._NameOID.COMMON_NAME, host)]))
                .issuer_name(self._ca_cert.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(self._ca_key, self._hashes.SHA256())
            )
            safe = host.replace("/", "_").replace(":", "_")
            cert_path = self._certs_dir / f"{safe}.pem"
            key_path = self._certs_dir / f"{safe}-key.pem"
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.set_alpn_protocols(["http/1.1"])
            context.load_cert_chain(str(cert_path), str(key_path))
            self._contexts[host] = context
            return context
