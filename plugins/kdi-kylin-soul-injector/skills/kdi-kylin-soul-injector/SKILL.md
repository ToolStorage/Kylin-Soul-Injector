---
name: kdi-kylin-soul-injector
description: Use when creating, changing, reviewing, or diagnosing Unity C# built with Kylin DI (`com.kylin.di`) and KDI Layered (`com.kylin.di.layered`), including layer ownership, Scope/LifetimeScope boundaries, bindings, Data/Domain/Application/ViewModel/View types, EntryPoints, dynamic GameObject or plain-C# lifetimes, disposal, async cleanup, and architecture validation. Do not use for projects that do not use KDI.
---

# KylinSoulInjector

Build against the installed KDI implementation, not remembered or project-fork APIs. Keep every dependency edge visible, give every stateful object one owner, and make teardown provable.

## Hard gates

1. Read the applicable `AGENTS.md` files, Unity package manifests, and the installed KDI source before editing.
2. Treat runtime source as authoritative. Treat READMEs, samples, and this skill's baseline as supporting evidence.
3. Do not invent container abilities. The KDI 2.0 baseline has transactional injection, Scope-owned injectable/disposable transients, reverse-activation cleanup, Unity injection leases, and Scope ownership of clones created by `IInstantiator`; it still has no phased EntryPoints, generic `IInstantiator.Create/Release`, pooling, or ownership of arbitrary/external Unity objects. Confirm the installed source.
4. Keep business dependencies in exactly one KDI layer. Do not use Resolve, static access, callbacks, or a global event bus to hide a forbidden edge.
5. Define a layer, Scope, lifetime, creator, and teardown path for every changed stateful type before implementing it.

## Load

- Always read [kdi-contract.md](references/kdi-contract.md).
- Read [layer-patterns.md](references/layer-patterns.md) for Data, services, ViewModels, Views, ownership, or dependency repairs.
- Read [lifecycle-patterns.md](references/lifecycle-patterns.md) for Scope design, dynamic creation, Unity objects, subscriptions, async work, or disposal.
- Read [verification.md](references/verification.md) before finishing architecture or lifecycle work, or when the audit reports findings.

## Workflow

### 1. Ground the graph

- Locate `Packages/manifest.json`, `Packages/packages-lock.json`, embedded packages, or vendored KDI source.
- Inspect `ScopeBuilder`, `Scope`, `LifetimeScope`, `IInstantiator`, `LayerValidator`, layer interfaces, and the analyzer diagnostics that are actually installed.
- Trace existing consumers, bindings, creation sites, parent Scope, and disposal sites with targeted search.
- Record the proposed graph in this compact form:

```text
Type | Layer | Owning Scope | Lifetime | Created by | Torn down by
```

- Reuse an existing owner or Scope boundary when its lifetime matches. Do not create a parallel graph.

### 2. Shape layers and ownership

- Preserve the direction `View -> ViewModel -> ApplicationService -> DomainService -> Data`; skipping downward is allowed.
- Reject same-layer and upward injection. Move shared state/logic down, or move multi-owner coordination up.
- Keep mutable state in Data. Expose reads through read-only contracts and place `[OwnerOnly]` mutations behind `IDomainServiceLayer<TData>`.
- Put one complete user operation or multi-domain transaction in ApplicationService.
- Keep Unity objects and presentation lifecycle in View. Keep ViewModel free of `GameObject`, `Transform`, and `MonoBehaviour` ownership.
- Classify every injected business contract. Allow an unclassified injected port only for `IInstantiator` or a narrow infrastructure/config boundary already present in the project; document that exception.

### 3. Choose the shortest correct Scope

- Put app-lifetime infrastructure in the root Scope; use `AsSingleton()` only there.
- Put session, scene, match, screen, popup, or feature state in a matching child Scope with `AsScoped()`.
- Use `AsTransient()` only when a fresh managed identity is required. KDI 2.0 rejects update-loop and `UnityEngine.Object` transients, and retains every injectable and/or container-created disposable transient until Scope shutdown, so use a shorter child Scope for high-volume tracked instances.
- Treat `FromInstance()` and public direct injection of an external object as permanent external identity: the Scope manages the injection lease and field restoration, but the supplier remains responsible for the object's own `Dispose()` and a later factory cannot take ownership. KDI observes every `FromInstance` identity before activation, so binding order cannot transfer it to a factory; a same-Scope factory may expose that identity only as external-owned.
- Let children resolve parents. Never make a parent cache a child service or resolver. Dispose a child to replace short-lived state without rebuilding its parent.

### 4. Wire through KDI

- Use private field injection and the correct marker interface. Use `PostInject()` only for deterministic local setup after all fields are available; put injection-derived cleanup that needs those fields in `IPreUninjectable.PreUninject()`.
- Complete every binding before `Build()`. Prefer `To<T>()`, `ToSelf()`, `FromInstance()`, and `AlsoBind<T>()` over manual wiring.
- Keep KDI-layer dependencies out of constructors. `FromFactory` is zero-argument in KDI 2.0: capture only external creation values, never a resolver, then let KDI inject fields.
- Use `AsEntryPoint()` only for non-transient eager activation. Make multiple EntryPoints order-independent; express a required order as an injection edge or one proper coordinator.
- Permit `IScope.Resolve` only in an explicit composition boundary for immediate graph startup. Never inject a resolver or call `LifetimeScope.Find` from business/View code.
- Treat injection failure as an exception and graph rollback, not a log-only partial success. Use `TryInject` only when the caller has an explicit recovery path.
- Public `Inject`, `InjectGameObject`, and `Instantiate` paths require KDI's concrete `Scope`; `ScopeBuilder.Build(customParent)` is rejected for the same reason. A custom `IScope` cannot participate in the shared activation/lifetime ledger.
- Treat unexpected destruction of a cached Unity service as Scope-fatal. KDI disposes that owning Scope so already injected consumers are revoked. Savepoint-aware transaction validation rejects destruction of a hostless target, an earlier injected sibling, or a Scope-owned clone during activation. The global monitor catches callback-free destruction, and every managed update callback rechecks its owning Scope plus ancestors so a same-phase destroy cannot leak through a transitive C# dependency. Outside activation, a destroyed hostless public-injection target releases only its external injection lease. Use external-owned GameObjects for independently destroyed objects.
- Perform public update registration only through the canonical `UpdateLoopManager.Instance`; scene-added or directly constructed managers are not ownership authorities. Do not mix public registration with Scope-managed or direct-injected ownership for the same identity, and do not manually register or unregister during activation. KDI rejects these because Scope uninject could otherwise leave a callback running with restored fields and public player-loop mutations cannot roll back. Use `FromInstance` when an external identity needs both injection and managed update ownership, and dispose its Scope instead of publicly unregistering it.

### 5. Create and release objects

- Use the installed `IInstantiator` API only after the current graph activation has committed. KDI 2.0 rejects `Instantiate` inside a factory, `PostInject`, or another activation; otherwise it stages the clone inactive, prepares nested `LifetimeScope` runtime parents, injects it transactionally, and records the clone as owned by the concrete Scope.
- Retain the owning Scope as the teardown handle. Scope shutdown deactivates and destroys a successful `IInstantiator` clone; failed preparation/injection destroys it during rollback. Early Unity destruction is also safe because `InjectionLifetimeHost` removes the lifetime record.
- Do not return an `IInstantiator` clone to a pool across its owning Scope's lifetime: there is no release API and that Scope will destroy the clone. For pooling, let the pool create/own the GameObject and use `InjectGameObject` with a short concrete Scope whose disposal revokes injection before return.
- Permit `LifetimeScope` prefab roots: parentless scopes in the prefab become runtime children of the nearest prefab scope or calling Scope. Preserve the nested scope as an injection boundary; the calling Scope still owns and destroys the instantiated prefab clone.
- Create variable-count plain C# domain objects through a scoped owner that explicitly releases them. If each object needs its own DI graph, build and dispose a child Scope at a composition boundary.
- Never let a created object locate its own Scope or inject itself.

### 6. Close every lifetime

- Make scoped services that own subscriptions, callbacks, native handles, timers, or child objects implement `IDisposable`.
- Cancel owned async work on Dispose and guard every continuation before state or Unity side effects.
- Account for KDI 2.0 cleanup order: children and activation records are processed in reverse order; injection cleanup runs before KDI-created `IDisposable.Dispose`, then fields are restored. Keep tightly order-sensitive resources under one owner so graph changes cannot silently alter their relative activation order.
- Remember that `DIBehaviour._cd` covers each injected active interval, while `InjectionDisposables` and `OnBeforeUninject` cover the complete injection lease. It is push-injected, not a cached Scope-owned service.

### 7. Verify

- Run the bundled read-only audit:

```text
python <skill-root>/scripts/audit_kdi_architecture.py <unity-project-root>
```

- Treat every result as advisory triage: inspect `HIGH` signals first and explicitly investigate `UNKNOWN` results instead of assuming either compliance or violation. The script defaults to a non-blocking exit; only use `--fail-on` when the repository intentionally adopts that policy.
- Resolve final architecture truth with the installed Roslyn analyzer, compiler, runtime source, and tests. Then run the repository's documented compile and test commands; do not substitute an undeclared external integration.
- Confirm zero `KDI001`, `KDI002`, and `KDI003` analyzer errors. Review `KDI004`-`KDI009` preview warnings against the installed `2.0-preview.1` contract instead of treating them as runtime-enforced facts.
- Exercise affected Scope boundaries: build, resolve, child override, child Dispose, parent survival, parent cascade, and repeated rebuild.
- Inspect the final diff and report anything not actually verified.

## Output contract

Report changed files, the final ownership ledger, layer/scope decisions, creation and teardown paths, KDI-version deviations, audit/compile/test evidence, and any remaining unverified behavior.
