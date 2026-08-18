# Verification

Use three independent gates. Do not claim a gate passed unless it actually ran.

## 1. Static architecture audit

Run from any directory with a Python 3 launcher:

```text
python <skill-root>/scripts/audit_kdi_architecture.py <unity-project-root>
```

For an embedded package outside `Assets`, add it explicitly:

```text
python <skill-root>/scripts/audit_kdi_architecture.py <unity-project-root> --include Packages/MyGamePackage
```

The script is read-only and uses only the standard library. Its findings have `ADVISORY` authority: review high-confidence signals and record decisions, but use Roslyn, the compiler, and runtime tests to decide correctness. Namespace/alias/partial or scan-coverage ambiguity is emitted as `UNKNOWN` rather than synthesized into a violation. The default exit code is zero even when signals exist.

For stable composition boundaries, copy `audit-config.example.json` to `<project>/.kdi-audit.json` and narrow `boundary_globs` to real boundary files/directories. Filename-only boundary inference is reported as `KSI108 UNKNOWN`.

Only opt into CI gating deliberately:

```text
python <skill-root>/scripts/audit_kdi_architecture.py <unity-project-root> --fail-on error
```

`--legacy-blocking` is a compatibility alias for `--fail-on error`. Operational failures such as a missing project, invalid config, or unreadable path still return exit code 2. The result schema, authority contract, and one-to-one rule-to-ID registry are recorded in `audit-contract.json`. Treat those IDs as stable meanings: `KSI011` remains direct construction of a KDI-managed type outside a composition boundary and is not reused for a lifecycle rule.

For KDI 2.0 lifetime triage, `KSI010` identifies an update-loop transient and `KSI012` a Unity-object transient that the runtime rejects, `KSI109` identifies a valid managed injectable/disposable transient retained until Scope shutdown, and `KSI110` asks for proof of the external disposer for a disposable `FromInstance` registration. These remain advisory signals; confirm the resolved types in compiled code.

## 2. Compile and analyzer gate

- Run the repository's documented Unity compile/build command.
- Require zero compiler errors and zero `KDI001`, `KDI002`, and `KDI003` diagnostics.
- For analyzer contract `2.0-preview.1`, treat `KDI004`-`KDI007` and `KDI009` as warning-level review signals until an installed contract promotes them. `KDI008` is reserved for the removed scope-parameter factory rule and is not reported by the KDI 2.0 analyzer.
- Exercise a missing registration and require a thrown injection/resolve failure, unchanged target fields, and rollback of dependencies created earlier in the same activation. The warning-only path is a type with `[Inject]` fields that does not implement `IInjectable`.
- Run `LayerValidator.ValidateAssembly` in an existing architecture test or startup validation surface when the project already supports it.
- Do not add a new external automation dependency merely to run this gate. If no command is available, report compile/runtime validation as not run.

## 3. Lifecycle behavior gate

Exercise only the affected boundaries, but include each applicable invariant:

| Case | Required observation |
|---|---|
| Build | All required bindings resolve; `PostInject` runs once per successfully injected target; failed construction leaves no cached partial graph. |
| Child lookup | Child resolves its registration first and parent registrations otherwise. |
| Child Dispose | Child `IDisposable` services clean up once; parent remains usable. |
| Parent Dispose | Live children are disposed; their later calls fail or remain inert. |
| Rebuild | A second child graph has fresh Scoped state and no callback/update/static residue from the first. |
| Reverse cleanup | Injection cleanup runs before KDI-created owner `Dispose`, activation records unwind in reverse order, and one cleanup error does not skip the rest. |
| FromInstance | Scope revokes injection/restores fields but does not call the imported instance's `Dispose`; its external owner does so once. |
| Transient | Updatable and Unity-object transients are rejected; each managed injectable/disposable transient is retained and cleaned by the shortest intended Scope. |
| `IInstantiator` clone | Injection precedes active-prefab `Awake`; successful clone is Scope-owned and is deactivated/destroyed at shutdown; early destruction releases its lifetime record. |
| Activation guard | `Instantiate` from a factory, `PostInject`, or another activation fails before a clone is created. |
| Custom `IScope` | Public `Inject`, `InjectGameObject`, `Instantiate`, and `ScopeBuilder.Build(customParent)` fail fast; no field, lease, clone, or untracked child graph is left behind. |
| Cached Unity service destruction | The owning Scope is disposed and every existing consumer injection is revoked; no destroyed service reference remains in an injected field, including a transitive dependency destroyed by an earlier callback in the same managed update phase. |
| Unity destruction during activation | Destroying a hostless target, an earlier injected sibling, or a Scope-owned clone makes the relevant transaction/savepoint fail; no partial hierarchy is returned as successful. |
| Hostless external injection destruction | During activation the transaction fails and rolls back; outside activation the next Update/FixedUpdate/LateUpdate boundary revokes only that external injection lease, leaving its otherwise valid Scope live. |
| Update ownership | A non-canonical manager, public/manual and Scope-managed or direct-injected ownership of the same identity, public unregister of a Scope-owned identity, and public register/unregister during activation fail fast; unexpected manager destruction transfers registrations and lifetime monitors. |
| External injected object | `InjectGameObject` does not transfer GameObject ownership; Scope revokes injection and the explicit Unity owner destroys/pools it. |
| LifetimeScope prefab | Parentless prefab scope receives the calling/nearest runtime parent and parent shutdown cascades without requiring a serialized link. |
| Async | Completion after Dispose cannot mutate Data, call back, or touch Unity objects. |
| Subscription | Reopen/rebuild produces one callback, not duplicates. |

Use the project's existing test framework where available. A focused runtime smoke test is acceptable when no automated harness exists, but record exactly what was observed.

## Final diff gate

Search the changed area and inspect the diff for:

```text
[Inject]
Bind< / FromInstance / FromFactory / AlsoBind / AsEntryPoint
AsSingleton / AsScoped / AsTransient
IScope / Resolve / LifetimeScope.Find
IInstantiator / Object.Instantiate / Object.Destroy
Subscribe / callbacks / IDisposable / async
```

Confirm:

- Every injected business contract has one layer.
- Every binding is installed before the correct Scope Build.
- Every stateful object appears in the ownership ledger.
- Every creator has a matching teardown path.
- No constructor/factory/locator hides a layer edge.
- No result relies on an API absent from the installed KDI source.

Report the audit command/result (including `ADVISORY`/`UNKNOWN` counts and any opt-in fail policy), compile/test command/result, lifecycle cases exercised, warnings accepted with reasons, and unverified items.
