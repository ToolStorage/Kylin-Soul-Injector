# KDI contract baseline

Use this as a compact compatibility baseline for `com.kylin.di` 1.4.0 and `com.kylin.di.layered` 1.1.2. Inspect the installed package source first. If it differs, follow that source and report the difference.

## Establish the installed contract

Check, in order:

1. Embedded or vendored runtime source used by the Unity assembly.
2. `Packages/packages-lock.json` and package `package.json` versions.
3. `Packages/manifest.json` declarations.
4. Package README and samples.

Read these symbols before changing architecture: `ScopeBuilder`, `Scope`, `LifetimeScope`, `DependencyInjector`, `DependencyBuilder`, `IInstantiator`, layer marker interfaces, `LayerValidator`, and analyzer diagnostics.

## Core DI guarantees

| Surface | Baseline behavior |
|---|---|
| Registration | `ScopeBuilder` is mutable before `Build()` and one-use/frozen afterward. Incomplete or duplicate bindings fail. |
| `To<T>()` | Requires `IDependencyObject`, assignability to the service contract, and a public parameterless constructor. |
| `ToSelf()` | Requires a concrete `IDependencyObject` with a public parameterless constructor. |
| `FromInstance()` | Requires `IDependencyObject`; Build caches, injects, and activates it eagerly. The Scope owns and disposes it. |
| `FromFactory()` | KDI injects the returned object after creation. Do not Resolve layered dependencies inside the factory because analyzers cannot see those edges. |
| `AlsoBind<T>()` | Maps multiple contracts to the same cached instance. Separate bindings create separate instances. |
| Injection | Only `[Inject]` instance fields on an `IInjectable` target are injected. Resolution is all-or-nothing per target. `PostInject()` runs after successful field assignment. |
| Missing dependency | `DependencyInjector` logs the failed field and skips all field assignment for that target; the baseline does not rethrow. Inspect runtime logs. |
| Lookup | Resolution searches current Scope first, then parents. A child registration overrides the same parent contract. |
| Circular dependency | Resolve tracks the chain and throws. |
| EntryPoint | `AsEntryPoint()` eagerly resolves a non-transient registration during Scope construction. Injection dependencies determine prerequisite creation. No phase API exists in the baseline. |

## Lifetime guarantees

| Lifetime | Ownership and restrictions |
|---|---|
| Singleton | Cached once and allowed only in a root Scope. Root Dispose releases it. |
| Scoped | Cached once in the registering Scope. That Scope releases it. |
| Transient | Created per Resolve, not cached, not Scope-disposed, and not registered in KDI update loops. Use only when no tracked lifetime is needed. |
| FromInstance | Treated as Scoped ownership even though creation happened outside KDI. Do not reuse it from another owner. |

- A child Scope is attached to its parent. Parent Dispose cascades to children; child Dispose detaches itself and leaves the parent alive.
- Scope Dispose calls `IDisposable` once per cached reference and unregisters update-loop interfaces.
- Baseline disposal order is not a public guarantee and disposal exceptions are swallowed. Put order-sensitive cleanup under one explicit owner and make Dispose non-throwing.
- Transient `IUpdatable`, `IFixedUpdatable`, or `ILateUpdatable` implementations are rejected. A known transient `IDisposable` implementation produces a warning but remains creator-owned.
- KDI does not cancel tasks, detach external callbacks, clear static state, or destroy Unity objects automatically.

## Unity integration

- `LifetimeScope.Initialize()` builds its Scope, initializes its parent first, push-injects itself and descendants, and stops traversal at nested `LifetimeScope` objects.
- A parentless `LifetimeScope` becomes the root. Its serialized parent link defines the Unity Scope hierarchy.
- `LifetimeScope.OnDestroy()` disposes its Scope. Calling `Scope.Dispose()` alone does not destroy the `LifetimeScope` GameObject or its hierarchy.
- `DIBehaviour` is push-injected and exposes a protected Scope. Its `CompositeDisposable` is disposed and replaced on `OnDisable`. It is not a cached Scope-owned service.
- `LifetimeScope.Find` and `DIBehaviour.Scope.Resolve` exist in the baseline, but using them in runtime feature code creates a service locator. Restrict Resolve to explicit composition boundaries.

Baseline `IInstantiator` supports only:

```text
Instantiate(GameObject)
Instantiate(GameObject, Transform)
Instantiate(GameObject, Vector3, Quaternion)
Instantiate(GameObject, Vector3, Quaternion, Transform)
InjectGameObject(GameObject)
```

It injects the selected Scope into a GameObject hierarchy. It does not retain, release, pool, disable, or destroy the object. Injection stops at a `LifetimeScope` boundary.

## Layered guarantees

```text
View -> ViewModel -> ApplicationService -> DomainService -> Data
```

- A layer may inject only a lower layer; it may skip layers.
- Same-layer and upward field injection are errors.
- Business types should implement exactly one marker: `IViewLayer`, `IViewModelLayer`, `IApplicationServiceLayer`, `IDomainServiceLayer`, or `IDataLayer`.
- Data owns state. `IDomainServiceLayer<TData>` declares mutation ownership for a Data type.
- `[OwnerOnly]` on a Data method/property restricts calls to its declared Domain owner. It is opt-in.
- Unclassified injected dependencies are not rejected by the baseline validator. Keep them limited to KDI intrinsic creation or narrow existing infrastructure/config ports.

The shipped analyzer reports exactly:

| ID | Meaning |
|---|---|
| `KDI001` | Same-layer `[Inject]` field |
| `KDI002` | Upward `[Inject]` field |
| `KDI003` | `[OwnerOnly]` member called by a non-owner |

`LayerValidator.Validate` and `ValidateAssembly` check field direction at runtime. They do not validate `OwnerOnly` call sites; only the Roslyn analyzer can do that.

## Do not infer extensions

The baseline does not provide EntryPoint phases, composition-entrypoint markers, cross-cutting attributes, debug-only resolvers, sealed runtime Resolve, generic C# `Create/Release`, automatic owned GameObjects, or reverse-order Dispose. Use such APIs only when the installed source explicitly defines them.
