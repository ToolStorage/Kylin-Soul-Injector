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

The script is read-only and uses only the standard library. Errors block completion. Warnings require inspection and a recorded decision. The audit checks high-confidence textual/structural risks; it does not replace Roslyn or runtime tests.

## 2. Compile and analyzer gate

- Run the repository's documented Unity compile/build command.
- Require zero compiler errors and zero `KDI001`, `KDI002`, and `KDI003` diagnostics for the baseline Layered package.
- Inspect logs for `[KDI]` injection failures. Missing registrations can be logged without throwing in the baseline runtime.
- Run `LayerValidator.ValidateAssembly` in an existing architecture test or startup validation surface when the project already supports it.
- Do not add a new external automation dependency merely to run this gate. If no command is available, report compile/runtime validation as not run.

## 3. Lifecycle behavior gate

Exercise only the affected boundaries, but include each applicable invariant:

| Case | Required observation |
|---|---|
| Build | All required bindings resolve; no KDI injection log; `PostInject` runs once per Scoped instance. |
| Child lookup | Child resolves its registration first and parent registrations otherwise. |
| Child Dispose | Child `IDisposable` services clean up once; parent remains usable. |
| Parent Dispose | Live children are disposed; their later calls fail or remain inert. |
| Rebuild | A second child graph has fresh Scoped state and no callback/update/static residue from the first. |
| Transient | No stateful, updatable, or disposable object relies on Scope cleanup. |
| Unity object | View is disabled/destroyed explicitly; no injected GameObject remains after its boundary closes. |
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

Report the audit command/result, compile/test command/result, lifecycle cases exercised, warnings accepted with reasons, and unverified items.
