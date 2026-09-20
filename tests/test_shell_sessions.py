# SPDX-License-Identifier: Apache-2.0
"""Lo stato delle sessioni: tetto, scadenza, output e storico senza bugie.

Qui non c'e' nessun processo: sono le regole che rendono una sessione uno stato
di cui fidarsi — e che dicono cosa e' stato buttato via invece di far finta che
non ci fosse mai stato.
"""
import unittest

from shared.shell_sessions import RingBuffer, SessionError, SessionStore


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def store(**kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    return SessionStore(clock=clock, **kwargs), clock


class RingBufferTests(unittest.TestCase):
    def test_keeps_everything_under_the_limit(self):
        buffer = RingBuffer(1024)
        buffer.write("ciao")
        buffer.write(" mondo")
        text, dropped = buffer.read()
        self.assertEqual(text, "ciao mondo")
        self.assertEqual(dropped, 0)
        self.assertEqual(len(buffer), len("ciao mondo"))

    def test_drops_the_oldest_and_counts_what_was_dropped(self):
        buffer = RingBuffer(1024)
        buffer.write("a" * 1000)
        buffer.write("b" * 100)
        text, dropped = buffer.read()
        self.assertEqual(dropped, 76)               # 1100 - 1024, il piu' vecchio
        self.assertEqual(len(text.encode("utf-8")), 1024)
        self.assertTrue(text.endswith("b" * 100))

    def test_read_can_clear_and_then_the_counter_starts_again(self):
        buffer = RingBuffer(1024)
        buffer.write("x" * 1100)
        buffer.read(clear=True)
        self.assertEqual(buffer.read(), ("", 0))
        self.assertEqual(len(buffer), 0)

    def test_multibyte_text_is_counted_in_bytes(self):
        buffer = RingBuffer(1024)
        buffer.write("è" * 400)                     # 2 byte per carattere
        self.assertEqual(len(buffer), 800)
        text, dropped = buffer.read()
        self.assertEqual(dropped, 0)
        self.assertEqual(text, "è" * 400)


class SessionStoreTests(unittest.TestCase):
    def test_open_returns_an_id_and_the_cwd(self):
        sessions, _ = store()
        session = sessions.open("/repo", label="prova")
        self.assertTrue(session.id.startswith("sx-"))
        self.assertEqual(session.cwd, "/repo")
        self.assertEqual(session.label, "prova")
        self.assertEqual([s["session_id"] for s in sessions.list()], [session.id])

    def test_a_cap_on_open_sessions_is_a_number_not_a_hope(self):
        sessions, _ = store(max_sessions=1)
        sessions.open("/repo")
        with self.assertRaises(SessionError) as ctx:
            sessions.open("/repo")
        self.assertIn("SHELL_MAX_SESSIONS", str(ctx.exception))

    def test_idle_sessions_expire_and_say_so(self):
        sessions, clock = store(idle_s=60)
        session = sessions.open("/repo")
        clock.advance(61)
        with self.assertRaises(SessionError) as ctx:
            sessions.get(session.id)
        self.assertIn("scaduta", str(ctx.exception))
        self.assertEqual(sessions.list(), [])

    def test_using_a_session_keeps_it_alive(self):
        sessions, clock = store(idle_s=60)
        session = sessions.open("/repo")
        for _ in range(3):
            clock.advance(50)
            sessions.get(session.id)
        self.assertEqual(len(sessions.list()), 1)

    def test_close_returns_a_summary_worth_logging(self):
        sessions, clock = store()
        session = sessions.open("/repo")
        session.record(["git", "status"], ok=True, exit_code=0, duration_ms=12, now=clock())
        session.output.write("on branch main\n")
        clock.advance(5)
        summary = sessions.close(session.id)
        self.assertTrue(summary["closed"])
        self.assertEqual(summary["commands"], 1)
        self.assertEqual(summary["cwd"], "/repo")
        self.assertGreaterEqual(summary["duration_s"], 5)
        self.assertIn("output_bytes", summary)
        with self.assertRaises(SessionError):
            sessions.get(session.id)

    def test_the_history_is_bounded_and_says_how_many_it_dropped(self):
        sessions, clock = store(history=2)
        session = sessions.open("/repo")
        for index in range(5):
            session.record(["python", f"-c{index}"], ok=True, exit_code=0,
                           duration_ms=1, now=clock())
        self.assertEqual([item["argv"][1] for item in session.history], ["-c3", "-c4"])
        self.assertEqual(session.history_dropped, 3)
        self.assertEqual(session.summary(clock())["commands"], 5)

    def test_describe_is_for_the_panel(self):
        sessions, _ = store(max_sessions=3, idle_s=120)
        sessions.open("/repo")
        described = sessions.describe()
        self.assertEqual(described["open"], 1)
        self.assertEqual(described["max_sessions"], 3)
        self.assertEqual(described["idle_s"], 120)
        self.assertEqual(len(described["sessions"]), 1)

    def test_the_output_buffer_of_a_session_respects_the_configured_limit(self):
        sessions, _ = store(max_output_bytes=1024)
        session = sessions.open("/repo")
        session.output.write("y" * 2000)
        summary = session.summary(1000.0)
        self.assertEqual(summary["output_bytes"], 1024)
        self.assertEqual(summary["output_dropped"], 976)

    def test_an_unknown_id_is_refused_clearly(self):
        sessions, _ = store()
        with self.assertRaises(SessionError):
            sessions.get("sx-non-esiste")


if __name__ == "__main__":
    unittest.main()
