# AFC to SpanVouch Hard-Cutover Migration

SpanVouch is the public system name. IVAD is the research method. AFC is
historical provenance only; it is not an active public name or compatibility
surface.

| Former AFC surface | SpanVouch surface |
| --- | --- |
| Agent Failure Clinic / AFC | SpanVouch |
| `agent-failure-clinic` distribution | `spanvouch==0.2.0` |
| `afc` import root and `src/afc` | `spanvouch` and `src/spanvouch` |
| `afc-*` command wrappers | one `spanvouch` CLI with subcommands |
| `AFC_*` variables | `SPANVOUCH_*` variables |
| `afc.db` | `spanvouch.db` |
| `afc_*` Compose names | `spanvouch_*` Compose names |
| AFC API/product copy | SpanVouch API/product copy |

There is no `afc` import alias, `afc-*` wrapper, or `AFC_*` environment
fallback. Unknown legacy configuration fails explicitly rather than being
silently accepted.

## Allowed historical occurrences

The repository-wide scan may retain AFC text only in immutable provenance:

- Git history, commit messages, and the `phase3-frozen-20260718` marker;
- frozen Phase 1–3 datasets, manifests, reports, hash records, and their
  authentic generator/provenance fields;
- pre-Phase-4 design, handoff, plan, and acceptance documents; and
- this migration record, where the old/new mapping is required.

Historical artifacts retain their original AFC bytes. New SpanVouch manifests
refer to them by hash and parent-artifact identity; they do not rewrite history.
The active source, tests, packaging, delivery configuration, README, CI, and
container configuration use only SpanVouch names.
