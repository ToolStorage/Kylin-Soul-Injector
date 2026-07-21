# Scope, creation, and teardown patterns

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
| Stateless disposable-free helper | Transient only when repeated instances are required |
| Variable-count domain entity | Scoped Domain owner tracks plain entities |
| Injected GameObject | Explicit View/scope handle plus Unity Transform parent |

## Programmatic child Scope

Keep `IScope` in a composition-only handle. Resolve at most the immediate boundary facade after Build:

```csharp
public sealed class BattleScopeHandle : System.IDisposable
{
    private IScope _scope;

    public BattleScopeHandle(IScope parent, BattleSeed seed)
    {
        var builder = new ScopeBuilder();
        builder.Bind<BattleData>().FromInstance(new BattleData(seed));
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

The boundary owner creates and disposes this handle. Disposing it leaves `parent` live. Disposing `parent` also cascades to this child. Never expose `_scope` or store it in a layered service.

Because baseline Scope construction activates instance registrations and EntryPoints before returning, keep their setup deterministic and free of irreversible external side effects.

## Variable-count plain C# objects

The baseline `IInstantiator` cannot create/inject arbitrary C# objects. Keep them as plain objects owned by one Scoped service:

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
        {
            _projectiles[i].Dispose();
        }
        _projectiles.Clear();
    }
}
```

Pass required values into the plain entity. Do not pass a resolver. If every entity needs its own injected graph, create a child Scope per aggregate at a composition boundary and dispose its handle when the aggregate ends.

## Injected Unity View lifetime

KDI injection and Unity ownership are separate. Pair a child Scope with the View root explicitly:

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

        var instantiator = _scope.Resolve<IInstantiator>();
        _viewRoot = instantiator.Instantiate(prefab, unityParent);
    }

    public void Dispose()
    {
        if (_viewRoot != null)
        {
            _viewRoot.SetActive(false);
            Object.Destroy(_viewRoot);
            _viewRoot = null;
        }

        _scope?.Dispose();
        _scope = null;
    }
}
```

- Keep this handle at a composition/View boundary; do not register it as a business service.
- Deactivate the View first so `DIBehaviour.OnDisable` removes subscriptions before its services are disposed.
- Pass a Transform owned by the same Unity lifetime boundary. Also retain the handle; parenting alone does not make `Scope.Dispose()` destroy the object.
- Do not use a prefab root containing `LifetimeScope` with this pattern. Parent injection stops at that boundary, and the nested Scope must initialize through its own explicit hierarchy.
- Direct `Object.Destroy` is required for explicit cleanup in baseline KDI because `IInstantiator` has no release API.

## Subscriptions and callbacks

For a Scoped C# service:

```csharp
private readonly CompositeDisposable _subscriptions = new();

public void PostInject()
{
    _data.State.Subscribe(OnStateChanged).AddTo(_subscriptions);
}

public void Dispose()
{
    _subscriptions.Dispose();
    _externalSource.Changed -= OnExternalChanged;
}
```

For `DIBehaviour`, add subscriptions to `_cd`; KDI resets it on every `OnDisable`. Explicitly remove UnityEvent, static, native, or third-party callbacks that are not represented by an `IDisposable` subscription.

## Async lifetime guard

KDI does not cancel work. Own cancellation in the same Scoped service:

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

Check the guard after every external await and before Data, callback, or Unity side effects. Make Dispose idempotent and non-throwing because baseline Scope suppresses disposal exceptions.

## Rebuild invariant

For replaceable state, verify this sequence:

```text
build child A -> use A -> dispose A -> parent still works
-> build child B -> use B -> dispose parent -> B is inert/disposed
```

No object from A may remain in parent fields, static state, event subscribers, update loops, Unity hierarchy, or async continuations.
