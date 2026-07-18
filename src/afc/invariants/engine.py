from hashlib import sha256

from afc.invariants.models import InvariantResult, InvariantRule, RuleContext


class InvariantEngine:
    def __init__(self, rules: tuple[InvariantRule, ...]) -> None:
        identities = [(rule.rule_id, rule.rule_version) for rule in rules]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate invariant rule id and version")
        self._rules = rules
        version_source = "\n".join(
            f"{rule_id}@{rule_version}" for rule_id, rule_version in sorted(identities)
        )
        self._ruleset_version = sha256(version_source.encode("utf-8")).hexdigest()

    @property
    def ruleset_version(self) -> str:
        return self._ruleset_version

    def run(self, context: RuleContext) -> tuple[InvariantResult, ...]:
        return tuple(
            sorted(
                (rule.evaluate(context) for rule in self._rules),
                key=lambda result: (result.rule_id, result.rule_version),
            )
        )
