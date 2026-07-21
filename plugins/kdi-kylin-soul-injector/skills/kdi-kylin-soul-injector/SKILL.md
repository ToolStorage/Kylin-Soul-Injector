---
name: kdi-kylin-soul-injector
description: Use when creating, changing, reviewing, or diagnosing Unity C# built with Kylin DI (`com.kylin.di`) and KDI Layered (`com.kylin.di.layered`), including layer ownership, Scope/LifetimeScope boundaries, bindings, Data/Domain/Application/ViewModel/View types, EntryPoints, dynamic GameObject or plain-C# lifetimes, disposal, async cleanup, and architecture validation. Do not use for projects that do not use KDI.
---

# KylinSoulInjector

Build against the installed KDI implementation, not remembered or project-fork APIs. Keep every dependency edge visible, give every stateful object one owner, and make teardown provable.

## Hard gates

1. Read the applicable `AGENTS.md` files, Unity package manifests, and the installed KDI source before editing.
2. Treat runtime source as authoritative. Treat READMEs, samples, and this skill's baseline as supporting evidence.
3. Do not invent container abilities. In particular, never assume phased EntryPoints, generic `IInstantiator.Create/Release`, automatic GameObject destruction, transient disposal, or reverse disposal order unless the installed source implements them.
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
- Use `AsTransient()` only for stateless, non-updatable, non-`IDisposable` objects whose creator owns any resulting references.
- Transfer ownership deliberately with `FromInstance()`: the receiving Scope injects and disposes that instance.
- Let children resolve parents. Never make a parent cache a child service or resolver. Dispose a child to replace short-lived state without rebuilding its parent.

### 4. Wire through KDI

- Use private field injection and the correct marker interface. Use `PostInject()` only for deterministic local setup after all fields are available.
- Complete every binding before `Build()`. Prefer `To<T>()`, `ToSelf()`, `FromInstance()`, and `AlsoBind<T>()` over manual wiring.
- Keep KDI-layer dependencies out of constructors and `FromFactory` Resolve calls; those edges bypass analyzer visibility. Use factory arguments only for external creation values, then let KDI inject fields.
- Use `AsEntryPoint()` only for non-transient eager activation. Make multiple EntryPoints order-independent; express a required order as an injection edge or one proper coordinator.
- Permit `IScope.Resolve` only in an explicit composition boundary for immediate graph startup. Never inject a resolver or call `LifetimeScope.Find` from business/View code.

### 5. Create and release objects

- Use the installed `IInstantiator` API. In the baseline KDI API, it instantiates/injects GameObjects but does not own or destroy them.
- Parent injected GameObjects under an explicit Unity lifetime root and retain a teardown handle. Disable/destroy the View root before disposing its child Scope.
- Create variable-count plain C# domain objects through a scoped owner that explicitly releases them. If each object needs its own DI graph, build and dispose a child Scope at a composition boundary.
- Never let a created object locate its own Scope or inject itself.

### 6. Close every lifetime

- Make scoped services that own subscriptions, callbacks, native handles, timers, or child objects implement `IDisposable`.
- Cancel owned async work on Dispose and guard every continuation before state or Unity side effects.
- Do not rely on container disposal order. Put order-sensitive resources under one owner and release them explicitly in that owner.
- Remember that `DIBehaviour` clears its `CompositeDisposable` on `OnDisable`; it is push-injected, not a Scope-owned C# service.

### 7. Verify

- Run the bundled read-only audit:

```text
python <skill-root>/scripts/audit_kdi_architecture.py <unity-project-root>
```

- Fix all audit errors and inspect every warning. Then run the repository's documented compile and test commands; do not substitute an undeclared external integration.
- Confirm zero `KDI001`, `KDI002`, and `KDI003` analyzer errors when using the baseline Layered package.
- Exercise affected Scope boundaries: build, resolve, child override, child Dispose, parent survival, parent cascade, and repeated rebuild.
- Inspect the final diff and report anything not actually verified.

## Output contract

Report changed files, the final ownership ledger, layer/scope decisions, creation and teardown paths, KDI-version deviations, audit/compile/test evidence, and any remaining unverified behavior.
