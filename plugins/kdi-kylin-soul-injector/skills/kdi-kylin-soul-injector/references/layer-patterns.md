# Layer and ownership patterns

## Responsibility map

| Layer | Owns | Must not own |
|---|---|---|
| Data | Mutable/observable state and local invariants | Cross-domain workflow, Unity presentation |
| DomainService | Mutations for declared Data owner(s) | Another DomainService dependency, presentation |
| ApplicationService | One user operation across owners | Another ApplicationService dependency, Unity objects |
| ViewModel | Presentation state and commands | Unity objects, another ViewModel dependency |
| View | Unity objects and presentation lifecycle | Durable business state |

Prefer `IReadOnlySubscribableProperty<T>` on read contracts. Keep mutable `SubscribableProperty<T>` private.

## Typed Data owner

Use one concrete Data type for owner-only mutation and one read contract for other consumers:

```csharp
using Kylin.DI;
using Kylin.DI.Layered;
using Kylin.SubscribableProperty;

public interface IPlayerData : IDataLayer
{
    IReadOnlySubscribableProperty<int> Health { get; }
}

public sealed class PlayerData : IPlayerData
{
    private readonly SubscribableProperty<int> _health = new(100);

    public IReadOnlySubscribableProperty<int> Health => _health;

    [OwnerOnly]
    public void SetHealth(int value)
    {
        _health.Value = value;
    }
}

public interface IPlayerDomain : IDomainServiceLayer<PlayerData>
{
    void ApplyDamage(int amount);
}

public sealed class PlayerDomain : IPlayerDomain
{
    [Inject] private PlayerData _playerData;

    public void ApplyDamage(int amount)
    {
        var next = System.Math.Max(0, _playerData.Health.Value - amount);
        _playerData.SetHealth(next);
    }
}
```

Bind both Data contracts to one instance:

```csharp
builder.Bind<PlayerData>()
    .ToSelf()
    .AlsoBind<IPlayerData>()
    .AsScoped();
builder.Bind<IPlayerDomain>().To<PlayerDomain>().AsScoped();
```

Inject `PlayerData` only into its declared owner. Inject `IPlayerData` for read-only observation elsewhere.

## Application, ViewModel, and View

```csharp
public interface ICombatApplication : IApplicationServiceLayer
{
    void Attack(int damage);
}

public sealed class CombatApplication : ICombatApplication
{
    [Inject] private IPlayerDomain _playerDomain;

    public void Attack(int damage)
    {
        _playerDomain.ApplyDamage(damage);
    }
}

public interface IPlayerViewModel : IViewModelLayer
{
    IReadOnlySubscribableProperty<int> Health { get; }
    void Attack();
}

public sealed class PlayerViewModel : IPlayerViewModel
{
    [Inject] private IPlayerData _playerData;
    [Inject] private ICombatApplication _combatApplication;

    public IReadOnlySubscribableProperty<int> Health => _playerData.Health;

    public void Attack()
    {
        _combatApplication.Attack(10);
    }
}

public sealed class PlayerHud : DIBehaviour, IViewLayer
{
    [Inject] private IPlayerViewModel _viewModel;

    private void Start()
    {
        _viewModel.Health
            .Subscribe(UpdateHealth, invokeInitial: true)
            .AddTo(_cd);
    }

    private void UpdateHealth(int health)
    {
        // Update serialized Unity presentation fields here.
    }
}
```

Register each implementation in the Scope that matches its state lifetime. A screen-specific ViewModel is Scoped in a screen Scope, not Transient when it owns subscriptions or commands with state.

## Repair forbidden edges

| Bad edge | Repair |
|---|---|
| Domain A -> Domain B | Let an ApplicationService inject and coordinate both, or read B's Data contract without mutating it. |
| Application A -> Application B | Create one ApplicationService for the complete operation and inject the lower Domain owners it needs. |
| ViewModel A -> ViewModel B | Extract shared state/commands downward or let the View compose independent presentation pieces. |
| View A -> View B through DI | Let a parent View own serialized subview references or a view-local callback; do not turn View composition into service lookup. |
| Lower layer callback to upper layer | Expose read-only state or a narrow subscription from the lower layer; let the upper layer subscribe and dispose. |
| Any forbidden edge hidden by event bus/Resolve | Remove the indirection and model the actual owner/coordinator. |

## Factories and external values

Keep layered dependency edges as `[Inject]` fields. Use `FromInstance` or `FromFactory` only to supply values KDI cannot construct, such as a loaded configuration or serialized Unity reference:

```csharp
builder.Bind<FeatureConfig>()
    .FromFactory(() => new FeatureConfig(serializedValue))
    .AsScoped();
```

The factory is zero-argument. Capture only the external value; do not capture a scope and call `Resolve<T>()` to assemble a layered service. The object returned by the factory can still implement `IInjectable`, and KDI will inject its visible fields.

## EntryPoints

- Use an EntryPoint only when eager construction itself is required, such as activating a scoped update service.
- Keep `PostInject()` synchronous, deterministic, and local.
- Do not depend on binding enumeration order. An injected dependency is created first; unrelated EntryPoints must remain order-independent.
- For fallible or asynchronous startup, finish `Build()` first and invoke one Application boundary from the composition host so failure can dispose the now-owned Scope.
