# SPDX-License-Identifier: Apache-2.0
"""La policy dei comandi: cosa e' distruttivo, cosa no, e chi decide.

Il valore di questo modulo e' che le regole hanno un nome: se una cosa viene
chiesta in conferma, il perche' sta nel verdetto e nei log. Qui si verifica la
tabella delle regole, le tre soglie e i limiti dichiarati (il default e'
`write`, non `read`: eseguire puo' sempre scrivere).
"""
import os
import unittest

from shared import shell_policy
from shared.shell_policy import RISK_DESTRUCTIVE, RISK_READ, RISK_WRITE, ShellPolicy


def classify(argv):
    return ShellPolicy().classify(argv)


class RuleTableTests(unittest.TestCase):
    def test_destructive_commands_carry_the_rule_name(self):
        cases = {
            ("rm", "-rf", "build"): "rm_ricorsivo",
            ("rm", "-r", "node_modules"): "rm_ricorsivo",
            ("rm", "*"): "rm_globale",
            ("rmdir", "src"): "rm_ricorsivo",
            ("del", "/s", "cartella"): "del_ricorsivo",
            ("remove-item", "-Recurse", "-Force", "cartella"): "remove_item_ricorsivo",
            ("git", "push"): "git_push",
            ("git", "push", "--force", "origin", "main"): "git_push",
            ("git", "reset", "--hard", "HEAD~1"): "git_reset_hard",
            ("git", "clean", "-fd"): "git_clean",
            ("git", "filter-branch", "--tree-filter", "true", "HEAD"): "git_riscrittura_storia",
            ("git", "branch", "-D", "feature"): "git_branch_delete",
            ("git", "stash", "drop"): "git_stash_drop",
            ("git", "checkout", "--", "."): "git_checkout_scarta_modifiche",
            ("npm", "publish"): "pubblicazione_pacchetto",
            ("docker", "system", "prune", "-f"): "docker_prune",
            ("docker", "rm", "abc123"): "docker_distruttivo",
            ("chmod", "-R", "777", "."): "permessi_ricorsivi",
            ("taskkill", "/F", "/PID", "123"): "terminazione_processi",
            ("shutdown", "-h", "now"): "spegnimento",
            ("systemctl", "restart", "docker"): "servizio_di_sistema",
            ("dd", "if=/dev/zero", "of=/dev/sda"): "operazione_su_disco",
            ("mkfs.ext4", "/dev/sdb1"): "operazione_su_disco",
        }
        produced = set()
        for argv, rule in cases.items():
            with self.subTest(argv=argv):
                verdict = classify(list(argv))
                self.assertEqual(verdict.risk, RISK_DESTRUCTIVE)
                self.assertEqual(verdict.rules, [rule])
            produced.add(rule)
        # La copertura e' verificata, non dichiarata: ogni nome distruttivo del
        # vocabolario viene prodotto da almeno un comando di questa tabella.
        self.assertEqual(produced, set(shell_policy.DESTRUCTIVE_NAMES))

    def test_read_only_commands_are_not_flagged(self):
        for argv in (("ls", "-la"), ("git", "status", "--short"), ("git", "log", "--oneline"),
                     ("git", "diff"), ("git", "stash"), ("cat", "README.md"),
                     ("find", ".", "-name", "*.py"), ("node", "--version"),
                     ("git", "--version"), ("python", "-V")):
            with self.subTest(argv=argv):
                self.assertEqual(classify(list(argv)).risk, RISK_READ)

    def test_everything_else_is_a_write_not_a_read(self):
        """`pytest`, `python -c`, `npm install`, `git commit`: eseguono codice o
        scrivono, quindi non si dichiarano letture."""
        for argv in (("pytest", "-q"), ("python", "-c", "import os"),
                     ("npm", "install"), ("git", "commit", "-m", "x"),
                     ("git", "checkout", "-b", "nuovo"), ("node", "script.js")):
            with self.subTest(argv=argv):
                verdict = classify(list(argv))
                self.assertEqual(verdict.risk, RISK_WRITE)
                self.assertFalse(verdict.requires_confirmation)

    def test_a_not_destructive_rm_stays_a_write(self):
        verdict = classify(["rm", "vecchio.txt"])
        self.assertEqual(verdict.risk, RISK_WRITE)
        self.assertEqual(verdict.rules, ["comando_di_scrittura"])

    def test_windows_style_names_are_understood(self):
        verdict = classify([r"C:\Program Files\Git\cmd\git.EXE", "push"])
        self.assertEqual(verdict.risk, RISK_DESTRUCTIVE)
        self.assertEqual(verdict.rules, ["git_push"])

    def test_empty_argv_is_refused(self):
        with self.assertRaises(ValueError):
            classify([])


class ThresholdTests(unittest.TestCase):
    def test_by_default_only_destructive_asks_for_confirmation(self):
        policy = ShellPolicy()
        self.assertTrue(policy.check(["ls"]).allowed)
        self.assertTrue(policy.check(["pytest", "-q"]).allowed)
        refused = policy.check(["git", "push"])
        self.assertFalse(refused.allowed)
        self.assertTrue(refused.requires_confirmation)
        self.assertIn("git_push", refused.error)
        self.assertTrue(policy.check(["git", "push"], confirm=True).allowed)

    def test_none_turns_the_question_off_completely(self):
        policy = ShellPolicy(confirm_at="none")
        self.assertTrue(policy.check(["git", "push"]).allowed)

    def test_write_level_makes_every_write_ask(self):
        policy = ShellPolicy(confirm_at=RISK_WRITE)
        self.assertTrue(policy.check(["ls", "-la"]).allowed)
        self.assertFalse(policy.check(["npm", "install"]).allowed)
        self.assertTrue(policy.check(["npm", "install"], confirm=True).allowed)

    def test_blocking_wins_over_confirmation(self):
        """Un runtime non presidiato non deve poter distruggere nemmeno con un
        `confirm=true` di troppo."""
        policy = ShellPolicy(block_at=RISK_DESTRUCTIVE)
        verdict = policy.check(["rm", "-rf", "/"], confirm=True)
        self.assertTrue(verdict.blocked)
        self.assertFalse(verdict.allowed)
        self.assertIn(shell_policy.BLOCK_VAR, verdict.error)

    def test_from_env_reads_both_switches_and_reports_bad_values(self):
        policy = ShellPolicy.from_env({shell_policy.CONFIRM_VAR: "write",
                                       shell_policy.BLOCK_VAR: "destructive"})
        self.assertEqual(policy.confirm_at, RISK_WRITE)
        self.assertEqual(policy.block_at, RISK_DESTRUCTIVE)
        self.assertEqual(policy.problems, [])
        broken = ShellPolicy.from_env({shell_policy.CONFIRM_VAR: "forse"})
        self.assertEqual(broken.confirm_at, RISK_DESTRUCTIVE)
        self.assertTrue(any(shell_policy.CONFIRM_VAR in problem for problem in broken.problems))

    def test_describe_has_no_secrets_and_names_the_rules(self):
        described = ShellPolicy().describe()
        self.assertEqual(described["confirm_at"], RISK_DESTRUCTIVE)
        self.assertEqual(described["block_at"], "none")
        self.assertIn("git_push", described["rules"])
        self.assertEqual(set(described), {"confirm_at", "block_at", "rules", "problems"})

    def test_the_verdict_dict_is_what_the_log_and_the_caller_see(self):
        verdict = ShellPolicy().check(["git", "push"])
        payload = verdict.to_dict()
        self.assertEqual(set(payload), {"risk", "rules", "requires_confirmation",
                                       "blocked", "allowed"})
        self.assertFalse(payload["allowed"])
        self.assertIn("git_push", repr(verdict))

    def test_from_env_without_arguments_reads_the_real_environment(self):
        self.assertIn(ShellPolicy.from_env().confirm_at, {RISK_WRITE, RISK_DESTRUCTIVE, "none"})
        self.assertIn(os.name, {"nt", "posix"})


if __name__ == "__main__":
    unittest.main()

