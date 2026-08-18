# KDI contract baseline

Use this as the verified compatibility baseline for `com.kylin.di` 2.0.0 and `com.kylin.di.layered` 2.0.0 (analyzer contract `2.0-preview.1`). Inspect the installed source and analyzer artifact first; when they differ, follow the installed implementation and report the difference.

## Establish the installed contract

Check, in order:

1. Embedded or vendored runtime source compiled by the Unity assembly.
2. The loaded analyzer DLL and its `ContractVersion`.
3. `Packages/packages-lock.json` and package `package.json` versions.
4. `Packages/manifest.json` declarations.
5. Package README and samples as non-authoritative supporting material.

Read these symbols before changing architecture: `ScopeBuilder`, `Scope`, `LifetimeScope`, `DependencyInjector`, `DependencyBuilder`, `InjectionLease`, `InjectionLifetimeHost`, `DIBehaviour`, `IInstantiator`, layer marker interfaces, `LayerValidator`, and analyzer diagnostics.

## Core DI guarantees

| Surface | KDI 2.0 behavior |
|---|---|
| Registration | `ScopeBuilder` is mutable before `Build()` and one-use/frozen afterward. Incomplete, duplicate, invalid alias, and reserved `IInstantiator` bindings fail. |
| `To<T>()` | Requires `IDependencyObject`, assignability to the service contract, and a public parameterless constructor through its generic constraints. |
| `ToSelf()` | Requires a concrete `IDependencyObject` with a public parameterless constructor. |
| `FromInstance()` | Requires `IDependencyObject`; Build caches, injects, and activates it eagerly. It remains externally owned: Scope revokes its injection and restores fields, but does not call its `Dispose()`. |
| `FromFactory()` | Accepts a zero-argument `Func<T>`. Capture only external creation values, never a resolver. KDI treats a successful returned object as container-created, injects it, and owns its `IDisposable` lifetime, including a transient result. |
| `AlsoBind<T>()` | Maps all declared contracts to the same registration and cached instance. Separate bindings create separate instances. |
| Injection | Inherited `[Inject]` instance fields on an `IInjectable` target are resolved before any field is assigned. `PostInject()` runs only after assignment. Same-Scope reinjection is idempotent; a second Scope cannot inject the target until the first lease is revoked. |
| Public injection boundary | `Inject`, `InjectGameObject`, and `Instantiate` require KDI's concrete `Scope`. `ScopeBuilder.Build(customParent)` is also rejected. A custom `IScope` fails with `NotSupportedException` before mutation because it has no shared activation/lifetime ledger. |
| Missing dependency | Resolve failure is wrapped and rethrown. The target is not partially assigned; the activation transaction revokes earlier injections, restores fields, disposes KDI-created rollback objects, and removes their cached entries. Use `TryInject` only when an explicit boolean/error path is intended. |
| Non-injectable target | A type that declares `[Inject]` fields but does not implement `IInjectable` is skipped and warned. This is the warning-only case; it is not missing-registration recovery. |
| Lookup | Resolution searches the current Scope first, then parents. A child registration overrides the same parent contract. |
| Circular dependency | The activation transaction tracks `(Scope, Registration)` and throws with the resolution path. |
| EntryPoint | `AsEntryPoint()` eagerly resolves a non-transient registration during Scope construction. Dependency resolution defines prerequisite creation; no phase API exists. |

All Scope, injection, lifetime, and update-loop APIs enforce main-thread access. Building a child Scope or disposing a Scope from inside an activation factory or `PostInject()` is rejected. `Instantiate` is likewise rejected while any activation transaction is in progress, including a factory, `PostInject()`, or nested activation.

## Lifetime and rollback guarantees

| Lifetime/source | Ownership and restrictions |
|---|---|
| Singleton | Cached once in a root Scope; registration in a child Scope is rejected. It is not a process-global singleton across separate isolated roots. A KDI-created `IDisposable` is disposed by that root. |
| Scoped | Cached once in the Scope that owns the registration. A KDI-created `IDisposable` is disposed by that Scope. |
| Transient | Created on every Resolve and never cached. A KDI-created managed transient is Scope-owned: KDI retains a successful `IInjectable` lease and/or KDI-created `IDisposable`, then revokes/disposes it at Scope shutdown. High-volume tracked transients therefore stay retained until that Scope ends. A transient `UnityEngine.Object` is rejected at Resolve. |
| FromInstance / direct external Inject | Cached `FromInstance` objects and identities first introduced through public direct injection remain external-owned. All `FromInstance` identities are observed before activation, so registration order cannot transfer ownership; a same-Scope factory can only expose the identity without claiming it. KDI revokes callbacks/injection and restores fields, while the supplying owner remains responsible for the instance's own `Dispose()`. |

- A child Scope attaches to its parent. Parent Dispose cascades through live children in reverse attachment order; child Dispose detaches and leaves the parent live.
- Within each Scope, committed activation records are cleaned in reverse activation order: unregister update-loop participation, run injection cleanup, dispose each distinct KDI-created `IDisposable`, then restore injected fields.
- `IPreUninjectable.PreUninject()` runs while injected fields are still available. It can also run after `PostInject()` started and threw, so it must tolerate partial setup and be idempotent.
- Scope cleanup continues after individual errors and logs one aggregate rather than rethrowing it. Cleanup callbacks and `Dispose()` implementations should still be idempotent and non-throwing.
- Transient `IUpdatable`, `IFixedUpdatable`, `ILateUpdatable`, and `UnityEngine.Object` instances are rejected. Known managed transient `IInjectable`/`IDisposable` implementations warn because Scope retention can grow until shutdown.
- Unexpected destruction of a cached Unity service invalidates and disposes its owning Scope so fields in existing consumers are revoked instead of retaining a destroyed reference. Hostless destruction during activation is rejected at the transaction boundary; callback-free service or external-injection destruction is polled. Before each managed update callback KDI also checks the owning Scope and its ancestors, preventing a hostless Unity service destroyed by an earlier callback from leaking through a transitive C# dependency in the same phase.
- Public/manual registration is accepted only by canonical `UpdateLoopManager.Instance` and may not share an identity with Scope-managed or direct-injected ownership. Public registration and unregistration are forbidden during activation, and a Scope-managed registration is released by disposing its owning Scope. Use `FromInstance` for an external identity that needs both injection and managed update ownership. Unexpected manager destruction transfers update ownership and Unity lifetime monitors to a replacement.
- KDI does not cancel tasks, detach callbacks that user cleanup did not register, clear application static state, or destroy arbitrary/external Unity objects. The narrower `IInstantiator` clone-ownership rule below is deliberate.

## Unity integration

- `LifetimeScope.Initialize()` initializes its declared/runtime parent first, builds its Scope, registers a primary root when applicable, push-injects itself and descendants, and stops traversal at nested `LifetimeScope` boundaries.
- A parentless `LifetimeScope` is `Primary` by default. Only one primary root may be active; `Isolated` roots do not replace it. Compatibility access can create an empty `AutoRootScope`, which a later primary root replaces and disposes.
- Initialization is transactional. If configuration, entry-point activation, or hierarchy injection fails, the failed Scope is disposed and the exception escapes.
- `LifetimeScope.OnDestroy()` disposes its Scope. Disposing a live `LifetimeScope`/its concrete Scope deactivates the GameObject and marks cascade children for reinitialization when the parent is initialized again; it does not destroy the hierarchy.
- A `LifetimeScope` subclass must not redeclare parameterless `Awake` or `OnDestroy`; validation throws because those messages bypass the base lifecycle.
- `DIBehaviour` owns two cleanup intervals: `_cd` for each injected active interval and `InjectionDisposables` for the complete injection lease. `OnInjectedDisable()` and `OnBeforeUninject()` run before fields are restored. Subclasses must use these hooks rather than redeclare `OnEnable`/`OnDisable`.

`IInstantiator` provides:

```text
Instantiate(GameObject)
Instantiate(GameObject, Transform)
Instantiate(GameObject, Vector3, Quaternion)
Instantiate(GameObject, Vector3, Quaternion, Transform)
InjectGameObject(GameObject)
```

`Instantiate` requires a concrete KDI `Scope` and cannot run inside a factory, `PostInject()`, or another activation. Otherwise instantiation occurs under an inactive staging root, the clone is recorded as Scope-owned, runtime parents for nested `LifetimeScope` components are prepared, the non-scope hierarchy is injected, and only then is the instance moved into its final hierarchy. An active prefab therefore observes completed injection before its `Awake`/`OnEnable`. If preparation or injection fails, rollback destroys the new instance and rethrows.

Prefab roots and descendants containing `LifetimeScope` are supported. A parentless scope inside the prefab becomes a runtime child of the nearest prefab scope or the calling Scope; its own initialization remains the injection boundary.

`IInstantiator.Instantiate` transfers clone ownership to the concrete KDI `Scope`. Scope shutdown first deactivates owned hierarchies while injected fields are still available to `OnDisable`, then unwinds activation records and destroys the owned clone. If Unity destroys the clone earlier, `InjectionLifetimeHost` releases its Component leases and removes the owned lifetime record so Scope shutdown does not clean it twice.

`InjectGameObject` is intentionally different: it records injection leases but does not take ownership of an externally created hierarchy. Its Unity owner must still destroy or pool that object after the concrete Scope has revoked injection. `IInstantiator` has no per-object release/pooling API, so an instantiated clone cannot be kept in a pool past disposal of its owning Scope.

## Layered guarantees

```text
View -> ViewModel -> ApplicationService -> DomainService -> Data
```

- A layer may inject only a lower layer; it may skip layers.
- Same-layer and upward field injection are errors.
- Business types should resolve to exactly one marker: `IViewLayer`, `IViewModelLayer`, `IApplicationServiceLayer`, `IDomainServiceLayer`, or `IDataLayer`. Inherited markers and generic constraints count.
- Data owns state. `IDomainServiceLayer<TData>` declares mutation ownership for a Data type.
- `[OwnerOnly]` on a Data method/property restricts calls to a Domain owner of the receiver's static Data contract. Overrides, interface contracts, accessors, and method references are part of the check.
- Unclassified injected dependencies are not rejected by the runtime validator. Keep them limited to KDI intrinsic creation or narrow existing infrastructure/config ports.

The `2.0-preview.1` analyzer contract reports:

| ID | Severity | Meaning |
|---|---|---|
| `KDI001` | Error | Same-layer `[Inject]` field |
| `KDI002` | Error | Upward `[Inject]` field |
| `KDI003` | Error | `[OwnerOnly]` member used by a non-owner |
| `KDI004` | Warning/preview | Type resolves to multiple KDI layers |
| `KDI005` | Warning/preview | `OwnerOnly` declaration or interface/override contract cannot be enforced consistently |
| `KDI006` | Warning/preview | Wildcard `IDomainServiceLayer<IDataLayer>` ownership |
| `KDI007` | Warning/preview | A layered type obtains a layered dependency through `Resolve` |
| `KDI008` | Reserved | Former scope-parameter factory rule; KDI 2.0's zero-argument factory no longer reports this diagnostic |
| `KDI009` | Warning/preview | Constructor injection or direct construction hides a layered dependency edge |

`LayerValidator.Inspect` reports field-direction findings and multi-layer ambiguity without throwing. `Validate`/`ValidateAssembly` throw only for `KDI001` and `KDI002`. Runtime validation does not inspect `OwnerOnly` call sites or hidden creation edges; those require the Roslyn analyzer.

## Do not infer extensions

KDI 2.0 does not provide EntryPoint phases, generic C# `IInstantiator.Create/Release`, pooling, implicit async cancellation, a runtime `OwnerOnly` call-site guard, or ownership of arbitrary/external GameObjects. It does own clones created by `IInstantiator.Instantiate`; use broader ownership APIs only when the installed source explicitly defines them.
