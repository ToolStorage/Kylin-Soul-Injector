from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_kdi_architecture.py"
SPEC = importlib.util.spec_from_file_location("audit_kdi_architecture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


class AuditSemanticResolutionTests(unittest.TestCase):
    def audit(self, files: dict[str, str], config=None):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            assets = project / "Assets"
            for relative, content in files.items():
                path = assets / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            sources = audit_module.read_sources(project, [assets])
            return audit_module.audit(project, sources, config or audit_module.AuditConfig())

    def test_diagnostic_id_registry_is_unique(self):
        diagnostic_ids = list(audit_module.DIAGNOSTIC_IDS.values())
        self.assertEqual(len(diagnostic_ids), len(set(diagnostic_ids)))
        contract = json.loads(
            (SCRIPT.parents[1] / "references" / "audit-contract.json").read_text(
                encoding="utf-8"
            )
        )
        contract_ids = {
            rule: entry["id"] for rule, entry in contract["diagnostics"].items()
        }
        self.assertEqual(audit_module.DIAGNOSTIC_IDS, contract_ids)
        self.assertEqual(
            "KSI011",
            audit_module.DIAGNOSTIC_IDS["direct_managed_construction"],
        )
        self.assertEqual(
            "KSI012",
            audit_module.DIAGNOSTIC_IDS["transient_unity_object"],
        )

    def test_implicit_base_closure_is_transitive_and_cycle_safe(self):
        self.assertIn(
            "UnityEngine.Object",
            audit_module.implicit_base_closure("Kylin.DI.DIBehaviour"),
        )

        cycle_a = "Tests.CycleA"
        cycle_b = "Tests.CycleB"
        audit_module.IMPLICIT_BASES[cycle_a] = {cycle_b}
        audit_module.IMPLICIT_BASES[cycle_b] = {cycle_a, "UnityEngine.Object"}
        try:
            self.assertEqual(
                {cycle_b, "UnityEngine.Object"},
                audit_module.implicit_base_closure(cycle_a),
            )
        finally:
            audit_module.IMPLICIT_BASES.pop(cycle_a, None)
            audit_module.IMPLICIT_BASES.pop(cycle_b, None)

    def test_namespace_collision_does_not_become_layer_violation(self):
        findings = self.audit(
            {
                "Collision.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
namespace Fake { public interface IDataLayer { } }
namespace Game
{
    public sealed class Host : Kylin.DI.Layered.IDataLayer
    {
        [Inject] private Fake.IDataLayer _fake;
    }
}
"""
            }
        )
        self.assertNotIn("KSI007", {finding.code for finding in findings})

    def test_ambiguous_simple_name_is_unknown_not_synthesized_error(self):
        findings = self.audit(
            {
                "Ambiguous.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
using A;
using B;
namespace A { public sealed class Shared : IDataLayer { } }
namespace B { public sealed class Shared : IViewModelLayer { } }
namespace Game
{
    public sealed class Host : IApplicationServiceLayer
    {
        [Inject] private Shared _shared;
    }
}
"""
            }
        )
        self.assertNotIn("KSI007", {finding.code for finding in findings})
        unknown = [finding for finding in findings if finding.code == "KSI107"]
        self.assertTrue(unknown)
        self.assertTrue(all(finding.state == "UNKNOWN" for finding in unknown))

    def test_alias_resolves_one_actual_type(self):
        findings = self.audit(
            {
                "Alias.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
using Selected = DomainTypes.ViewModel;
namespace DomainTypes { public sealed class ViewModel : IViewModelLayer { } }
namespace Game
{
    public sealed class Host : IDomainServiceLayer
    {
        [Inject] private Selected _upward;
    }
}
"""
            }
        )
        violations = [finding for finding in findings if finding.code == "KSI007"]
        self.assertEqual(1, len(violations))
        self.assertEqual("ADVISORY", violations[0].authority)
        self.assertEqual("HIGH", violations[0].confidence)

    def test_global_using_and_alias_apply_across_source_files(self):
        findings = self.audit(
            {
                "GlobalUsings.cs": """
global using Kylin.DI;
global using Kylin.DI.Layered;
global using Selected = DomainTypes.ViewModel;
""",
                "Types.cs": """
namespace DomainTypes { public sealed class ViewModel : IViewModelLayer { } }
namespace Game
{
    public sealed class Host : IDomainServiceLayer
    {
        [Inject] private Selected _upward;
    }
}
""",
            }
        )
        violations = [finding for finding in findings if finding.code == "KSI007"]
        self.assertEqual(1, len(violations))
        self.assertEqual("HIGH", violations[0].confidence)

    def test_enclosing_namespace_precedes_imported_simple_name(self):
        findings = self.audit(
            {
                "ParentNamespace.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
using Other;
namespace Company { public sealed class Shared : IViewModelLayer { } }
namespace Other { public sealed class Shared : IDataLayer { } }
namespace Company.Feature
{
    public sealed class Host : IDomainServiceLayer
    {
        [Inject] private Shared _upward;
    }
}
"""
            }
        )
        violations = [finding for finding in findings if finding.code == "KSI007"]
        self.assertEqual(1, len(violations))
        self.assertEqual("HIGH", violations[0].confidence)
        self.assertFalse(any(
            finding.code == "KSI107" and "Shared" in finding.message
            for finding in findings
        ))

    def test_partial_declarations_share_one_identity(self):
        findings = self.audit(
            {
                "Host.Layer.cs": """
using Kylin.DI.Layered;
namespace Game { public sealed partial class Host : IDataLayer { } }
""",
                "Host.Injection.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
namespace Game { public sealed partial class Host { [Inject] private IDataLayer _same; } }
""",
            }
        )
        self.assertEqual(1, sum(finding.code == "KSI007" for finding in findings))
        self.assertFalse(any("conflicting non-partial" in finding.message for finding in findings))

    def test_ambiguous_direct_new_is_not_a_hard_construction_finding(self):
        findings = self.audit(
            {
                "Construction.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
using A;
using B;
namespace A { public sealed class Managed : IDataLayer { } }
namespace B { public sealed class Managed { } }
namespace Game { public sealed class Runner { public object Run() => new Managed(); } }
"""
            }
        )
        self.assertNotIn("KSI011", {finding.code for finding in findings})
        self.assertTrue(any(finding.code == "KSI107" and finding.state == "UNKNOWN" for finding in findings))

    def test_direct_managed_construction_keeps_ksi011(self):
        findings = self.audit(
            {
                "Construction.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
namespace Game
{
    public sealed class Managed : IDependencyObject { }
    public sealed class Runner { public object Run() => new Managed(); }
}
"""
            }
        )
        direct = [finding for finding in findings if finding.code == "KSI011"]
        self.assertEqual(1, len(direct))
        self.assertIn("Direct construction", direct[0].message)
        self.assertNotIn("KSI012", {finding.code for finding in findings})

    def test_filename_boundary_is_unknown_but_explicit_glob_is_authoritative_for_audit(self):
        code = """
using Kylin.DI;
namespace Game { public sealed class Bootstrap { public object Run(IScope scope) => scope.Resolve<object>(); } }
"""
        heuristic = self.audit({"Bootstrap.cs": code})
        self.assertTrue(any(finding.code == "KSI108" for finding in heuristic))
        self.assertFalse(any(finding.code == "KSI004" for finding in heuristic))

        explicit = self.audit(
            {"Bootstrap.cs": code},
            audit_module.AuditConfig(boundary_globs=("Assets/Bootstrap.cs",)),
        )
        self.assertFalse(any(finding.code in {"KSI004", "KSI108"} for finding in explicit))

    def test_advisory_results_do_not_fail_without_opt_in(self):
        finding = audit_module.Finding(
            path="Assets/X.cs",
            line=1,
            severity="ERROR",
            code="KSI003",
            message="signal",
            confidence="HIGH",
            authority="ADVISORY",
            state="ADVISORY",
        )
        self.assertFalse(audit_module.should_fail([finding], "never"))
        self.assertTrue(audit_module.should_fail([finding], "error"))

    def test_transient_rules_match_kdi20_rejection_and_retention(self):
        findings = self.audit(
            {
                "Bindings.cs": """
using System;
using Kylin.DI;
namespace Kylin.DI
{
    public interface IDependencyObject { }
    public interface IInjectable { }
    public interface IUpdatable { }
    public interface IKDIUpdatable { }
}
namespace UnityEngine { public class Object { } public class Component : Object { } public class MonoBehaviour : Component { } }
namespace Game
{
    public sealed class Injected : IDependencyObject, IInjectable { }
    public sealed class Disposable : IDependencyObject, IDisposable { public void Dispose() { } }
    public sealed class Updating : IDependencyObject, IUpdatable { }
    public sealed class LegacyNamedOnly : IDependencyObject, IKDIUpdatable { }
    public sealed class UnityTransient : UnityEngine.MonoBehaviour, IDependencyObject { }
    public sealed class DirectBehaviourTransient : DIBehaviour, IDependencyObject { }
    public abstract class BehaviourBase : DIBehaviour { }
    public sealed class DerivedBehaviourTransient : BehaviourBase, IDependencyObject { }
    public sealed class Bindings
    {
        public void Configure(dynamic builder)
        {
            builder.Bind<Injected>().To<Injected>().AsTransient();
            builder.Bind<Disposable>().To<Disposable>().AsTransient();
            builder.Bind<Updating>().To<Updating>().AsTransient();
            builder.Bind<LegacyNamedOnly>().To<LegacyNamedOnly>().AsTransient();
            builder.Bind<UnityTransient>().To<UnityTransient>().AsTransient();
            builder.Bind<DirectBehaviourTransient>().To<DirectBehaviourTransient>().AsTransient();
            builder.Bind<DerivedBehaviourTransient>().To<DerivedBehaviourTransient>().AsTransient();
        }
    }
}
"""
            }
        )
        self.assertEqual(4, sum(finding.code == "KSI109" for finding in findings))
        self.assertEqual(1, sum(finding.code == "KSI010" for finding in findings))
        self.assertEqual(0, sum(finding.code == "KSI011" for finding in findings))
        self.assertEqual(3, sum(finding.code == "KSI012" for finding in findings))
        unity_messages = [
            finding.message for finding in findings if finding.code == "KSI012"
        ]
        self.assertTrue(any("DirectBehaviourTransient" in message for message in unity_messages))
        self.assertTrue(any("DerivedBehaviourTransient" in message for message in unity_messages))
        self.assertTrue(all(
            finding.severity == "WARN"
            for finding in findings
            if finding.code == "KSI109"
        ))
        self.assertTrue(all(
            finding.severity == "ERROR"
            for finding in findings
            if finding.code == "KSI010"
        ))
        self.assertTrue(all(
            finding.severity == "ERROR"
            for finding in findings
            if finding.code == "KSI012"
        ))

    def test_removed_iresolver_name_is_not_invented_as_a_kdi20_type(self):
        findings = self.audit(
            {
                "Resolver.cs": """
using Kylin.DI;
namespace Kylin.DI
{
    public sealed class InjectAttribute : System.Attribute { }
    public interface IInjectable { }
}
namespace Game
{
    public sealed class Host : IInjectable
    {
        [Inject] private IResolver _resolver;
    }
}
"""
            }
        )
        self.assertNotIn("KSI005", {finding.code for finding in findings})
        self.assertTrue(any(
            finding.code == "KSI107" and finding.state == "UNKNOWN"
            for finding in findings
        ))

    def test_versioned_config_loads_boundary_and_exit_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".kdi-audit.json").write_text(
                json.dumps(
                    {
                        "contract_version": audit_module.CONTRACT_VERSION,
                        "boundary_globs": ["Assets/Game/Composition/**"],
                        "framework_namespace_prefixes": ["Kylin.DI", "Company.Framework"],
                        "fail_on": "warning",
                    }
                ),
                encoding="utf-8",
            )

            config = audit_module.load_config(project, None)

            self.assertEqual(("Assets/Game/Composition/**",), config.boundary_globs)
            self.assertEqual(("Kylin.DI", "Company.Framework"), config.framework_namespace_prefixes)
            self.assertEqual("warning", config.fail_on)

    def test_disposable_from_instance_requires_external_teardown(self):
        findings = self.audit(
            {
                "Bindings.cs": """
using System;
using Kylin.DI;
namespace Kylin.DI { public interface IDependencyObject { } }
namespace Game
{
    public sealed class Imported : IDependencyObject, IDisposable
    {
        public void Dispose() { }
    }
    public sealed class Bindings
    {
        public void Configure(dynamic builder, Imported instance)
        {
            builder.Bind<Imported>().FromInstance(instance);
        }
    }
}
"""
            }
        )
        ownership = [finding for finding in findings if finding.code == "KSI110"]
        self.assertEqual(1, len(ownership))
        self.assertEqual("WARN", ownership[0].severity)
        self.assertEqual("HIGH", ownership[0].confidence)

    def test_zero_argument_factory_resolver_capture_is_advisory(self):
        findings = self.audit(
            {
                "Bindings.cs": """
using Kylin.DI;
using Kylin.DI.Layered;
namespace Game
{
    public sealed class Data : IDataLayer { }
    public sealed class Bindings
    {
        public void Configure(dynamic builder, IScope scope)
        {
            builder.Bind<Data>()
                .FromFactory(() => scope.Resolve<Data>())
                .AsScoped();
        }
    }
}
"""
            }
        )
        captured = [finding for finding in findings if finding.code == "KSI105"]
        self.assertEqual(1, len(captured))
        self.assertIn("zero-argument FromFactory", captured[0].message)


if __name__ == "__main__":
    unittest.main()
