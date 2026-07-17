from typing import Protocol

from afc.review.models import VerificationInput, VerifierKind, VerifierReport


class Verifier(Protocol):
    kind: VerifierKind
    version_fingerprint: str

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        raise NotImplementedError
