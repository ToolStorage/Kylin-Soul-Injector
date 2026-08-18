from __future__ import annotations

import json
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).parents[1]
CONTRACT = SKILL_ROOT / "references" / "kdi-contract.md"
LIFECYCLE = SKILL_ROOT / "references" / "lifecycle-patterns.md"
VERIFICATION = SKILL_ROOT / "references" / "verification.md"
AUDIT_CONTRACT = SKILL_ROOT / "references" / "audit-contract.json"
SKILL = SKILL_ROOT / "SKILL.md"
REPOSITORY_README = SKILL_ROOT.parents[3] / "README.md"


class Kdi20ReferenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = CONTRACT.read_text(encoding="utf-8")
        cls.lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        cls.verification = VERIFICATION.read_text(encoding="utf-8")
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.readme = REPOSITORY_README.read_text(encoding="utf-8")
        cls.audit_contract = json.loads(AUDIT_CONTRACT.read_text(encoding="utf-8"))
        cls.all_guidance = "\n".join(
            (cls.contract, cls.lifecycle, cls.verification, cls.skill, cls.readme)
        )

    def test_baseline_versions_and_analyzer_contract_are_current(self):
        self.assertIn("`com.kylin.di` 2.0.0", self.contract)
        self.assertIn("`com.kylin.di.layered` 2.0.0", self.contract)
        self.assertIn("`2.0-preview.1`", self.contract)
        self.assertIn("`com.kylin.di` 2.0.0", self.readme)
        self.assertIn("`com.kylin.di.layered` 2.0.0", self.readme)
        for diagnostic_number in range(1, 10):
            self.assertIn(f"`KDI{diagnostic_number:03d}`", self.contract)

    def test_external_instance_and_transient_ownership_match_kdi20(self):
        self.assertIn("It remains externally owned", self.contract)
        self.assertIn("does not call its `Dispose()`", self.contract)
        self.assertIn("KDI retains a successful `IInjectable` lease", self.contract)
        self.assertIn("supplier remains responsible for the object's own `Dispose()`", self.skill)
        self.assertIn("KSI010", self.audit_contract["kdi_2_lifecycle_signals"])
        self.assertIn("KSI012", self.audit_contract["kdi_2_lifecycle_signals"])
        self.assertIn("KSI109", self.audit_contract["kdi_2_lifecycle_signals"])
        self.assertIn("KSI110", self.audit_contract["kdi_2_lifecycle_signals"])

    def test_audit_rule_ids_are_unique_and_preserve_ksi011(self):
        diagnostics = self.audit_contract["diagnostics"]
        diagnostic_ids = [entry["id"] for entry in diagnostics.values()]
        self.assertEqual(len(diagnostic_ids), len(set(diagnostic_ids)))
        self.assertEqual("KSI011", diagnostics["direct_managed_construction"]["id"])
        self.assertEqual("KSI012", diagnostics["transient_unity_object"]["id"])
        self.assertIn("direct construction", diagnostics["direct_managed_construction"]["meaning"].lower())
        self.assertIn("UnityEngine.Object", diagnostics["transient_unity_object"]["meaning"])
        self.assertTrue(
            set(self.audit_contract["kdi_2_lifecycle_signals"]).issubset(diagnostic_ids)
        )

    def test_factory_contract_is_zero_argument_and_kdi008_is_reserved(self):
        self.assertIn("zero-argument `Func<T>`", self.contract)
        self.assertIn("`KDI008` | Reserved", self.contract)
        self.assertIn("KDI008` is reserved", self.verification)
        self.assertIn(".FromFactory(() =>", self.lifecycle)
        self.assertNotIn(".FromFactory(_ =>", self.all_guidance)

    def test_injection_failure_is_transactional_and_not_log_only(self):
        self.assertIn("Resolve failure is wrapped and rethrown", self.contract)
        self.assertIn("The target is not partially assigned", self.contract)
        self.assertIn("Treat injection failure as an exception and graph rollback", self.skill)
        self.assertIn("require a thrown injection/resolve failure", self.verification)

    def test_scope_owns_instantiator_clone_and_lifetime_scope_prefabs_are_supported(self):
        self.assertIn("Prefab roots and descendants containing `LifetimeScope` are supported", self.contract)
        self.assertIn("`InjectionLifetimeHost` releases its Component leases", self.contract)
        self.assertIn("transfers clone ownership to the concrete KDI `Scope`", self.contract)
        self.assertIn("Scope-owned instantiated clones were destroyed", self.lifecycle)
        self.assertIn("successful clone is Scope-owned", self.verification)
        self.assertIn("A prefab containing `LifetimeScope` is supported", self.lifecycle)
        self.assertIn("Parentless prefab scope receives the calling/nearest runtime parent", self.verification)

    def test_activation_internal_instantiation_and_custom_scope_fail_fast_are_documented(self):
        self.assertIn("cannot run inside a factory, `PostInject()`, or another activation", self.contract)
        self.assertIn("A custom `IScope` fails with `NotSupportedException` before mutation", self.contract)
        self.assertIn("`ScopeBuilder.Build(customParent)` is also rejected", self.contract)
        self.assertIn("A custom `IScope` cannot participate", self.skill)
        self.assertIn("fails before a clone is created", self.verification)
        self.assertIn("no field, lease, clone, or untracked child graph is left behind", self.verification)

    def test_obsolete_kdi14_claims_do_not_regress(self):
        obsolete_claims = (
            "`com.kylin.di` 1.4.0",
            "The Scope owns and disposes it.",
            "not cached, not Scope-disposed",
            "the baseline does not rethrow",
            "remains creator-owned",
            "receiving Scope injects and disposes that instance",
            "Do not use a prefab root containing `LifetimeScope`",
            "Missing registrations can be logged without throwing",
            "No stateful, updatable, or disposable object relies on Scope cleanup",
            "does not destroy/pool a successful instance",
            "KDI retains injection leases, not GameObject ownership",
            "Scope shutdown does not destroy a successfully instantiated GameObject",
            "Explicitly destroy or return the root to a pool",
            "automatic ownership/destruction of successfully instantiated GameObjects",
            "the explicit Unity owner still destroys/pools the object",
        )
        for claim in obsolete_claims:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, self.all_guidance)


if __name__ == "__main__":
    unittest.main()
