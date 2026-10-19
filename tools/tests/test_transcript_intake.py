"""Actual orchestration envelopes must not replace the owner's words."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcript as t
import extract_human_turns as extract


def user(text):
    return {'type': 'user', 'timestamp': '2026-10-06T12:07:36.780Z',
            'message': {'content': text}}


class IntakeTests(unittest.TestCase):
    def test_fixed_machine_openers_and_ordinary_human_text(self):
        for opener in t.ORCHESTRATOR_OPENERS:
            record = user(opener + '\nbody')
            self.assertEqual(t.classify_user(record, t.blocks(record['message']['content']), False), t.ORCHESTRATOR)
        record = user('Could you check the cron job and compactor?')
        self.assertEqual(t.classify_user(record, t.blocks(record['message']['content']), False), t.HUMAN)

    def test_pulse_preserves_a_long_message_and_ignores_wakes(self):
        message = 'You also cannot afford to let guesses win.\n' * 100 + '\nIn other words: intelligence.'
        prefix = t.PULSE_START + '\ncontext\n=== END PULSE PREAMBLE ===\n'
        context = '=== REPAIR INVARIANT ===\n- [14:07] quoted history\nobserved 2026-10-06T12:07:31+00:00\n\n'
        self.assertEqual(t.embedded_human(prefix + context + '[14:07] ' + message), (message, 'telegram-pulse'))
        self.assertIsNone(t.embedded_human(prefix + context + '[spawn-wake] process closed'))
        self.assertIsNone(t.embedded_human(t.PULSE_START + '\n[14:07] no closing boundary'))

    def test_runner_uses_the_last_marker_and_preserves_human_bold_paragraphs(self):
        message = 'A short question.\n\n**My own emphasis**\nAnd the rest.'
        body = ('[Substrate — runner-resolved, authoritative: opus]\n'
                "You're Shimmer, responding to [HUMAN] via Telegram.\n"
                '\n[HUMAN] says:\n- stale identity quotation\n'
                '\n[HUMAN] says:\n- ' + message + '\n\nRespond naturally — this is a phone chat.\n')
        self.assertEqual(t.embedded_human(body), (message, 'telegram-runner'))
        self.assertIsNone(t.embedded_human(body.replace('Respond naturally', 'unknown reply trailer')))

    def test_identical_turns_keep_provenance_and_full_human_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / 'project'
            project.mkdir()
            message = 'human words ' * 300
            prefix = t.PULSE_START + '\n=== END PULSE PREAMBLE ===\n[14:07] '
            for name in ('first', 'second'):
                records = [user(prefix + message)]
                (project / (name + '.jsonl')).write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')
            results = extract.collect(root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['human'], message.strip())
            self.assertEqual(len(results[0]['occurrences']), 2)
            self.assertEqual(results[0]['human_source'], 'telegram-pulse')
            self.assertEqual(extract.collect(root, until='2026-10-06'), [])

    def test_harness_and_child_prompts_do_not_yield_human_messages(self):
        record = user(t.PULSE_START)
        self.assertEqual(t.classify_user(record, t.blocks(t.PULSE_START), True), t.AGENT)
        record = user('Base directory for this skill: /skills/foo')
        self.assertEqual(t.classify_user(record, t.blocks(record['message']['content']), False), t.HARNESS)

    def test_whole_file_worker_briefs_require_worktree_and_worker_context(self):
        body = '# Fix brief — the bound-after-read build\nYou are the worker in this worktree; lapis reviews.'
        record = user(body)
        self.assertEqual(t.classify_user(record, t.blocks(body), False, '-Users-[HUMAN]-Projects-phil-contest-wt-bound'), t.ORCHESTRATOR)
        self.assertEqual(t.classify_user(record, t.blocks(body), False, '-Users-[HUMAN]-Projects-phil-contest'), t.HUMAN)
        ordinary = '# Brief — my thoughts\nI want to discuss the next steps.'
        self.assertEqual(t.classify_user(user(ordinary), t.blocks(ordinary), False, 'project-wt-test'), t.HUMAN)

    def test_provider_handoff_is_harness_input(self):
        body = 'Context handoff (manual_context):\nProvider context handoff. Thread: abc\n[Historical user] old words'
        self.assertEqual(t.classify_user(user(body), t.blocks(body), False), t.HARNESS)
        ide = '<ide_opened_file>The user opened style.css.</ide_opened_file>'
        self.assertEqual(t.classify_user(user(ide), t.blocks(ide), False), t.HARNESS)
        self.assertEqual(t.classify_user(user(ide + '\nPlease edit it.'), t.blocks(ide + '\nPlease edit it.'), False), t.HUMAN)

    def test_peer_followup_requires_an_identified_agent_dialogue(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'dialogue.jsonl'
            messages = ['We are collaborating on a UX/UI critique, not implementation. I am Codex, talking with you.',
                        'Round2 — challenge the first critique.', 'Heya, I have an ordinary question now.']
            path.write_text('\n'.join(json.dumps(user(body)) for body in messages), encoding='utf-8')
            self.assertEqual([turn['author'] for turn in t.load(path)], [t.ORCHESTRATOR, t.ORCHESTRATOR, t.HUMAN])
            self.assertEqual(t.classify_user(user(messages[1]), t.blocks(messages[1]), False), t.HUMAN)


if __name__ == '__main__':
    unittest.main()
