from __future__ import annotations

from typing import Protocol

from spanvouch.contracts.verification import VerificationInput, VerifierReport


class Verifier(Protocol):
    kind: str
    version_fingerprint: str

    async def verify(self, request: VerificationInput) -> VerifierReport:
        raise NotImplementedError
