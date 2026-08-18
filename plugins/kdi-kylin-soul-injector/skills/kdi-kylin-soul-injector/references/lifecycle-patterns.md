# Scope, creation, and teardown patterns

These patterns assume the verified KDI 2.0 runtime contract. Re-check the installed source before applying them to another version.

## Select the owner first

```text
Application Scope
  -> Session Scope
      -> Scene or Match Scope
          -> Screen, Popup, or Feature Scope
```

Create only boundaries the game actually has. Put a dependency in the shortest Scope that contains every consumer. A child may consume a parent dependency; a parent must not retain child services, callbacks, or `IScope` references.

| Object | Preferred ownership |
|---|---|
| App-wide platform/config service | Root Singleton |
| Session/scene/match state and services | Matching child Scoped |
| Stateful or disposable ViewModel | View/screen child Scoped |
| Repeated helper with no state or cleanup | Transient; KDI does not retain it when it is neither injectable nor disposable |
| Injectable or disposable transient | Short child Scope; KDI retains its lease/resource until that Scope closes |
| Unity object with independent destruction | External-owned GameObject plus explicit injection; transient Unity services are rejected |
| Variable-count domain entity | Scoped Domain owner tracks plain entities |
| `IInstantiator` clone | Calling KDI Scope owns it and deactivates/destroys it at shutdown |
| External GameObject passed to `InjectGameObject` | Explicit Unity owner destroys/pools it; KDI Scope owns only its injection lease |

## Programmatic child Scope

Keep `IScope` in a composition-only handle. Resolve at most the immediate boundary facade after Build:

```csharp
public sealed class BattleScopeHandle : System.IDisposable
{
    private IScope _scope;

    public BattleScopeHandle(IScope parent, BattleSeed seed)
    {
        var builder = new ScopeBuilder();
        builder.Bind<BattleData>()
            .FromFactory(() => new BattleData(seed))
            .AsScoped();
        builder.Bind<IBattleDomain>().To<BattleDomain>().AsScoped();
        builder.Bind<IBattleApplication>().To<BattleApplication>().AsScoped();

        _scope = builder.Build(parent, nameof(BattleScopeHandle));
        Application = _scope.Resolve<IBattleApplication>();
    }

    public IBattleApplication Application { get; }

    public void Dispose()
    {
        _scope?.Dispose();
        _scope = null;
    }
}
```

The zero-argument factory captures only the external creation value. It does not capture a resolver or Resolve layered dependencies; KDI field-injects those after creation. Because `FromFactory` is container-created, KDI owns an `IDisposable` result. Disposing the child leaves `parent` live, while disposing `parent` cascades to the child.

Scope construction observes every `FromInstance` identity before any activation, then eagerly injects those registrations and activates EntryPoints before returning. Binding order therefore cannot let a factory claim an explicitly external object. Keep this work deterministic. KDI rejects building another Scope, disposing a Scope, or calling `Instantiate` from inside a factory or `PostInject()`; `Instantiate` must begin after the owning graph has committed.

## External instance transfer is not ownership transfer

`FromInstance` and public direct injection make an identity injection-managed, but do not transfer its `IDisposable` ownership. That external identity classification remains after lease revocation, so a later factory cannot claim it:

```csharp
public sealed class ImportedSessionHandle : System.IDisposable
{
    private IScope _scope;
    private ExternalSessionState _state;

    public ImportedSessionHandle(IScope parent, ExternalSessionState state)
    {
        _state = state;
        var builder = new ScopeBuilder();
        builder.Bind<ExternalSessionState>().FromInstance(state);
        _scope = builder.Build(parent, nameof(ImportedSessionHandle));
    }

    public void Dispose()
    {
        _scope?.Dispose(); // PreUninject and injected-field restoration
        _scope = null;
        _state?.Dispose(); // the supplier/handle still owns the object
        _state = null;
    }
}
```

Do not dispose an imported instance from both the supplying owner and a consumer. If the Scope should own creation and disposal, use `To`, `ToSelf`, or `FromFactory` instead.

## Variable-count plain C# objects

`IInstantiator` creates GameObjects, not arbitrary C# objects. Keep variable-count entities as plain objects owned by one Scoped service:

```csharp
public sealed class ProjectileDomain : IProjectileDomain, System.IDisposable
{
    [Inject] private BattleData _battleData;

    private readonly System.Collections.Generic.List<Projectile> _projectiles = new();

    public Projectile Spawn(ProjectileArgs args)
    {
        var projectile = new Projectile(args);
        _projectiles.Add(projectile);
        return projectile;
    }

    public void Release(Projectile projectile)
    {
        if (!_projectiles.Remove(projectile)) return;
        projectile.Dispose();
    }

    public void Dispose()
    {
        for (var i = _projectiles.Count - 1; i >= 0; i--)
            _projectiles[i].Dispose();
        _projectiles.Clear();
    }
}
```

Pass required values into the plain entity. Do not pass a resolver. If every entity needs an injected graph, create a child Scope per aggregate at a composition boundary and close it when the aggregate ends; account for the retained activation ledger before doing this at high volume.

## Scope-owned instantiated Unity View lifetime

An `IInstantiator` clone belongs to the concrete KDI Scope that created it. Keep that Scope as the teardown handle:

```csharp
public sealed class ScopedViewHandle : System.IDisposable
{
    private IScope _scope;
    private GameObject _viewRoot;

    public ScopedViewHandle(
        IScope parent,
        GameObject prefab,
        Transform unityParent,
        System.Action<ScopeBuilder> configure)
    {
        var builder = new ScopeBuilder();
        configure(builder);
        _scope = builder.Build(parent, nameof(ScopedViewHandle));
        _viewRoot = _scope.Resolve<IInstantiator>().Instantiate(prefab, unityParent);
    }

    public void Dispose()
    {
        // Deactivates the owned hierarchy, revokes injection, and destroys the clone.
        _scope?.Dispose();
        _scope = null;
        _viewRoot = null;
    }
}
```

- Keep the handle at a composition/View boundary; do not register it as a business service.
- Build/resolve the graph first, then call `Instantiate`; a factory, `PostInject()`, or any other in-progress activation is rejected.
- Scope disposal deactivates every owned hierarchy first, so `DIBehaviour` active-interval cleanup and `OnDisable` can still see injected fields. It then revokes injection in reverse activation order and destroys the clone.
- Destroying the clone early is also safe: its hidden `InjectionLifetimeHost` releases Component leases and the owned-object record, so later Scope disposal does not clean it twice.
- Do not pool an `IInstantiator` clone across Scope shutdown. There is no per-object release API and the Scope will destroy it. For pooling, the pool must create/own the object; inject it through a short concrete Scope and dispose that Scope before return.
- A prefab containing `LifetimeScope` is supported. KDI prepares parentless prefab scopes as runtime children of the nearest prefab scope or calling Scope before activation. Parent Scope disposal cascades into those scopes before destroying the Scope-owned prefab clone.
- When the prefab's own `LifetimeScope` is the intended feature boundary, prefer configuring that scope over adding a redundant wrapper child Scope.

## Injection cleanup and subscriptions

For a KDI-created Scoped C# service, split cleanup by what needs injected fields:

```csharp
private CompositeDisposable _subscriptions = new();

public void PostInject()
{
    _data.State.Subscribe(OnStateChanged).AddTo(_subscriptions);
    _externalSource.Changed += OnExternalChanged;
}

public void PreUninject()
{
    _subscriptions.Dispose();
    _subscriptions = new CompositeDisposable();
    _externalSource.Changed -= OnExternalChanged;
}

public void Dispose()
{
    _ownedNativeHandle.Dispose();
}
```

Implement `IPostInjectable`, `IPreUninjectable`, and `IDisposable` as applicable. `PreUninject` runs before KDI calls `Dispose()` and before fields are restored. It may run after a partially failed `PostInject`, so every operation must tolerate partial setup and repeated cleanup attempts.

For `DIBehaviour`:

- Put active-only subscriptions in `_cd`; it is disposed on each injected disable and recreated before the next `OnInjectedEnable()`.
- Put resources that must survive disable/enable but end at injection revocation in `InjectionDisposables`.
- Use `OnInjectedEnable`, `OnInjectedDisable`, and `OnBeforeUninject`. Redeclaring parameterless `OnEnable` or `OnDisable` is rejected during injection.

## Async lifetime guard

KDI revokes injection and calls owned `Dispose()`, but it does not cancel work by itself. Own cancellation in the same Scoped service:

```csharp
private readonly System.Threading.CancellationTokenSource _lifetime = new();
private bool _disposed;

public async System.Threading.Tasks.Task RefreshAsync()
{
    var token = _lifetime.Token;
    var result = await _gateway.LoadAsync(token);
    if (_disposed || token.IsCancellationRequested) return;

    _domain.Apply(result);
}

public void Dispose()
{
    if (_disposed) return;
    _disposed = true;
    _lifetime.Cancel();
    _lifetime.Dispose();
}
```

Check the guard after every external await and before Data, callback, or Unity side effects. KDI 2.0 cleans activation records in reverse order and continues after cleanup errors, logging an aggregate; do not use that as permission for throwing cleanup code. Keep tightly order-dependent resources under one explicit owner so graph changes cannot silently alter their relative activation order.

## Rebuild invariant

For replaceable state, verify this sequence:

```text
build child A -> use A -> dispose A -> parent still works
-> build child B -> use B -> dispose parent -> B is inert/disposed
```

Also verify that injected fields from A were restored, `PreUninject` ran once, KDI-created transients were cleaned, imported `FromInstance` objects were not disposed, Scope-owned instantiated clones were destroyed, and externally destroyed Components released their leases before parent shutdown. No object from A may remain in parent fields, static state, event subscribers, update loops, Unity hierarchy, or async continuations.
