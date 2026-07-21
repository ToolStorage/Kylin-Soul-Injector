# KylinSoulInjector

Standalone Codex skill and plugin for Unity projects built with Kylin DI and KDI Layered. It guides implementation and review around layer direction, scope ownership, safe dynamic creation, explicit teardown, and fast structural verification.

Baseline compatibility:

- `com.kylin.di` 1.4.0
- `com.kylin.di.layered` 1.1.2
- Unity 6000.0 or newer
- Python 3 for the optional read-only architecture audit

The installed project's KDI runtime source always takes precedence over the bundled baseline reference. The plugin has no MCP, uloop, Unity Editor automation, or companion-skill dependency.

## Install with Codex CLI

Add the public Kylin marketplace and install the plugin:

```powershell
codex plugin marketplace add ToolStorage/Kylin-Soul-Injector
codex plugin add kdi-kylin-soul-injector@kylin
```

Start a new Codex thread, then invoke `$kdi-kylin-soul-injector` explicitly or let Codex select it for KDI architecture work.

## Update

Refresh the marketplace snapshot and reinstall the plugin:

```powershell
codex plugin marketplace upgrade kylin
codex plugin add kdi-kylin-soul-injector@kylin
```

Start a new thread after reinstalling so Codex loads the updated skill.

## Local development

From a local clone of this repository:

```powershell
codex plugin marketplace add C:\ProjectControl\KDI\KylinSoulInjector
codex plugin add kdi-kylin-soul-injector@kylin
```

The bundled `audit_kdi_architecture.py` script uses only the Python 3 standard library and never modifies the inspected project.

## Direct skill installation

If plugin installation is unavailable, `$skill-installer` can install the following folder from this repository:

```text
plugins/kdi-kylin-soul-injector/skills/kdi-kylin-soul-injector
```

Repository: https://github.com/ToolStorage/Kylin-Soul-Injector
