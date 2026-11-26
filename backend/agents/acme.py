"""A small ACME (RFC 8555) client: one DNS-01 certificate for one name.

Sparrow Connect gives this server a name; this gets the certificate for it.
The private key is made here and never leaves this server: the only thing
sent anywhere is the DNS proof value, which Sparrow Connect publishes.
"""

import asyncio
import base64
import hashlib
import json
import time

import httpx

LETS_ENCRYPT = "https://acme-v02.api.letsencrypt.org/directory"
LETS_ENCRYPT_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeError(Exception):
    pass


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_key_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def expiry(cert_pem: bytes) -> float:
    from cryptography import x509

    return x509.load_pem_x509_certificate(cert_pem).not_valid_after_utc.timestamp()


class Acme:
    def __init__(self, directory_url, account_key_pem, client: httpx.AsyncClient):
        from cryptography.hazmat.primitives import serialization

        self.directory_url = directory_url
        self.key = serialization.load_pem_private_key(account_key_pem, None)
        self.client = client
        self.directory = None
        self.kid = None
        self.nonces = []

    def jwk(self):
        numbers = self.key.public_key().public_numbers()
        return {
            "crv": "P-256",
            "kty": "EC",
            "x": b64(numbers.x.to_bytes(32, "big")),
            "y": b64(numbers.y.to_bytes(32, "big")),
        }

    def thumbprint(self):
        canonical = json.dumps(self.jwk(), sort_keys=True, separators=(",", ":"))
        return b64(hashlib.sha256(canonical.encode()).digest())

    def dns_value(self, token):
        """The TXT value that proves control of the name for this challenge."""
        authorization = f"{token}.{self.thumbprint()}"
        return b64(hashlib.sha256(authorization.encode()).digest())

    async def nonce(self):
        if self.nonces:
            return self.nonces.pop()
        response = await self.client.head(self.directory["newNonce"])
        return response.headers["Replay-Nonce"]

    def sign(self, protected, payload):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import (
            decode_dss_signature,
        )

        head = b64(json.dumps(protected).encode())
        body = "" if payload is None else b64(json.dumps(payload).encode())
        der = self.key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return {"protected": head, "payload": body, "signature": signature}

    async def post(self, url, payload, *, retry=True):
        """A signed request; payload None is a POST-as-GET."""
        protected = {"alg": "ES256", "nonce": await self.nonce(), "url": url}
        if self.kid:
            protected["kid"] = self.kid
        else:
            protected["jwk"] = self.jwk()
        response = await self.client.post(
            url,
            content=json.dumps(self.sign(protected, payload)),
            headers={"Content-Type": "application/jose+json"},
        )
        if "Replay-Nonce" in response.headers:
            self.nonces.append(response.headers["Replay-Nonce"])
        if response.status_code >= 400:
            try:
                problem = response.json()
            except ValueError:
                problem = {"detail": response.text[:300]}
            if retry and problem.get("type", "").endswith(":badNonce"):
                return await self.post(url, payload, retry=False)
            raise AcmeError(problem.get("detail") or f"Certificate request failed ({response.status_code}).")
        return response

    async def start(self):
        self.directory = (await self.client.get(self.directory_url)).raise_for_status().json()
        response = await self.post(
            self.directory["newAccount"], {"termsOfServiceAgreed": True}
        )
        self.kid = response.headers["Location"]

    async def poll(self, url, done, *, seconds=180):
        deadline = time.monotonic() + seconds
        while True:
            data = (await self.post(url, None)).json()
            if data["status"] in done:
                return data
            if data["status"] == "invalid":
                failures = [
                    c.get("error", {}).get("detail", "")
                    for c in data.get("challenges", [])
                    if c.get("error")
                ]
                raise AcmeError(
                    "The certificate check failed. " + " ".join(f for f in failures if f)
                )
            if time.monotonic() > deadline:
                raise AcmeError("The certificate check took too long. Try again.")
            await asyncio.sleep(2)

    async def certificate(self, name, publish, withdraw, key_pem):
        """Prove control of name through DNS, then return the PEM chain.

        publish(value) must return once the TXT record is visible to the CA;
        withdraw() removes it afterwards, whatever happened."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID

        if not self.kid:
            await self.start()
        response = await self.post(
            self.directory["newOrder"],
            {"identifiers": [{"type": "dns", "value": name}]},
        )
        order_url, order = response.headers["Location"], response.json()
        for url in order["authorizations"]:
            authorization = (await self.post(url, None)).json()
            if authorization["status"] == "valid":
                continue
            challenge = next(
                (c for c in authorization["challenges"] if c["type"] == "dns-01"), None
            )
            if not challenge:
                raise AcmeError("The certificate authority offered no DNS check.")
            await publish(self.dns_value(challenge["token"]))
            try:
                await self.post(challenge["url"], {})
                await self.poll(url, {"valid"})
            finally:
                await withdraw()
        key = serialization.load_pem_private_key(key_pem, None)
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .sign(key, hashes.SHA256())
        )
        await self.post(
            order["finalize"],
            {"csr": b64(csr.public_bytes(serialization.Encoding.DER))},
        )
        order = await self.poll(order_url, {"valid"})
        return (await self.post(order["certificate"], None)).content
