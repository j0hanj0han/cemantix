"""Tests Pédantix : dates (changement à midi), gabarit du titre, solveur, recalage des archives.

Lancement : venv/bin/python3 -m unittest tests.test_pedantix
"""

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import numpy as np

from core import PARIS_TZ
from games import pedantix as px
from games import pedantix_solver as ps

PAGE = """<html><body>
<script id="script" type="module" data-puzzle-number="1650" data-utc-time="39600"></script>
<div class="game" id="wiki"><h2><span class="h w">\xa0\xa0\xa0\xa0\xa0</span> <span class="h w">\xa0\xa0\xa0</span> \
<span class="h w">\xa0\xa0\xa0</span>'<span class="h w">\xa0\xa0\xa0\xa0\xa0</span> <img id="wiki-img"/></h2>
<div id="article"><p><span class="w">\xa0\xa0\xa0\xa0</span> <b><span class="w">\xa0\xa0\xa0\xa0\xa0</span></b>
<span class="w">\xa0\xa0\xa0\xa0\xa0\xa0</span>.</p></div></div></body></html>"""


class FakeModel:
    """Sous-ensemble de KeyedVectors utilisé par le solveur."""

    def __init__(self, vectors: dict[str, list[float]]):
        self.index_to_key = list(vectors)
        self.vector_size = len(next(iter(vectors.values())))
        self._v = {w: np.array(v, dtype=np.float32) for w, v in vectors.items()}

    def __contains__(self, w):
        return w in self._v

    def __getitem__(self, w):
        return self._v[w]


class PuzzleDateTests(unittest.TestCase):
    def test_dates_match_competitors(self):
        # gamewave.fr : #1640 = 8 novembre, #1630 = 29 octobre ; #1651 a commencé le 19/11 à midi
        self.assertEqual(px.puzzle_date(1640), date(2026, 11, 8))
        self.assertEqual(px.puzzle_date(1630), date(2026, 10, 29))
        self.assertEqual(px.puzzle_date(1651), date(2026, 11, 19))

    def test_current_puzzle_changes_at_noon(self):
        at = lambda *a: datetime(*a, tzinfo=PARIS_TZ)
        self.assertEqual(px.current_puzzle_num(at(2026, 11, 19, 0, 15)), 1650)
        self.assertEqual(px.current_puzzle_num(at(2026, 11, 19, 11, 59)), 1650)
        self.assertEqual(px.current_puzzle_num(at(2026, 11, 19, 12, 0)), 1651)
        self.assertEqual(px.current_puzzle_num(at(2026, 11, 20, 0, 15)), 1651)


class TemplateTests(unittest.TestCase):
    def test_parse_page(self):
        num, slots, template = ps.parse_page(PAGE)
        self.assertEqual(num, 1650)
        self.assertEqual(slots[:4], [3, 1, 1, 3])
        self.assertEqual(len(slots), 7)
        self.assertEqual([v for k, v in template if k == "slot"], [0, 1, 2, 3])
        self.assertEqual(ps.render_title(template, {0: "Tir", 1: "à", 2: "l", 3: "arc"}), "Tir à l'arc")
        self.assertEqual(ps.render_title(template, {1: "à"}, slots), "___ à _'___")

    def test_match_template(self):
        _, slots, template = ps.parse_page(PAGE)
        self.assertEqual(ps.match_template("Tir à l'arc", template, slots, {}),
                         {0: "Tir", 1: "à", 2: "l", 3: "arc"})
        self.assertIsNone(ps.match_template("Tir à l'arcs", template, slots, {}))      # longueur
        self.assertIsNone(ps.match_template("Tir à l arc", template, slots, {}))       # ponctuation
        self.assertIsNone(ps.match_template("Arc à l'arc", template, slots, {0: "Tir"}))  # mot déjà trouvé

    def test_match_template_parentheses(self):
        name = "Étienne Ier (roi de Hongrie)"
        template = [("slot", 0), ("txt", " "), ("slot", 1), ("txt", " ("), ("slot", 2), ("txt", " "),
                    ("slot", 3), ("txt", " "), ("slot", 4), ("txt", ")")]
        slots = [7, 3, 3, 2, 7]
        self.assertIsNotNone(ps.match_template(name, template, slots, {1: "Ier"}))


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel({
            "chat": [1, 0.1, 0], "chien": [0.8, 0.5, 0], "lion": [0.7, 0, 0.7],
            "arbre": [0, 1, 0], "pomme": [0, 0.7, 0.7], "loup": [0.1, 0.1, 1],
        })
        self.template = [("slot", 0)]

    def test_submit_parses_exact_and_scores(self):
        s = ps.PedantixSolver(self.model, 1, [4, 5], self.template,
                              score_fn=lambda w: {"x": {"#64.10": [0], "Arbre": [1]}}, log=lambda *a: None)
        s.submit("arbre")
        self.assertEqual(s.revealed, {1: "Arbre"})
        self.assertEqual(list(s.obs[0]), [0])
        self.assertAlmostEqual(s.obs[0][0], 0.641)
        self.assertFalse(s.solved())

    def test_best_candidate_triangulates(self):
        s = ps.PedantixSolver(self.model, 1, [4], self.template, score_fn=None, log=lambda *a: None)
        unit = lambda w: self.model[w] / np.linalg.norm(self.model[w])
        # observations cohérentes avec la cible « chat »
        for probe in ("chien", "arbre"):
            s.cache[probe] = {"x": {f"#{float(unit(probe) @ unit('chat')) * 100:.2f}": [0]}}
            s.submit(probe)
        self.assertEqual(s.best_candidate(0), "chat")

    def test_budget_stops_network_calls(self):
        calls = []
        s = ps.PedantixSolver(self.model, 1, [4], self.template, run_budget=2,
                              score_fn=lambda w: calls.append(w) or {"x": {}}, log=lambda *a: None)
        for w in ("chat", "lion", "loup"):
            s.submit(w)
        self.assertEqual(calls, ["chat", "lion"])


class ArchiveRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(px, "PEDANTIX_DIR", root),
            mock.patch.object(px, "PEDANTIX_ARCHIVE", root / "archive"),
        ]
        for p in self.patches:
            p.start()
        (root / "archive").mkdir()
        self.archive = root / "archive"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def write(self, ds, num, word, generated_at="2026-11-01T00:00:00+00:00"):
        (self.archive / f"{ds}.json").write_text(json.dumps(
            {"date": ds, "puzzle_num": num, "word": word, "generated_at": generated_at}))
        (self.archive / f"{ds}.html").write_text("old")

    def read(self, ds):
        return json.loads((self.archive / f"{ds}.json").read_text())

    def test_redate_shifts_and_dedups(self):
        # ancien code : #1646 le 15/11, #1648 en double les 16 et 17/11
        self.write("2026-11-15", 1646, "Pierre de Coubertin")
        self.write("2026-11-16", 1648, "Tourte voyageuse", "2026-11-17T20:25:00+00:00")
        self.write("2026-11-17", 1648, "Tourte voyageuse", "2026-11-17T23:16:00+00:00")
        changed = px._redate_archives()
        self.assertEqual(self.read("2026-11-14")["puzzle_num"], 1646)
        self.assertEqual(self.read("2026-11-16")["generated_at"], "2026-11-17T20:25:00+00:00")
        self.assertFalse((self.archive / "2026-11-15.json").exists())   # #1647 manquant → /history
        self.assertFalse((self.archive / "2026-11-17.json").exists())
        self.assertFalse((self.archive / "2026-11-17.html").exists())
        self.assertEqual(changed, 3)
        self.assertEqual(px._redate_archives(), 0)   # idempotent

    def test_sync_finished_fills_and_corrects(self):
        fake_hints = {"level1": [], "level2": [], "level3": [], "extract": "", "categories": []}
        self.write("2026-11-16", 1648, "Mauvais titre", "2026-11-16T11:05:00+00:00")
        with mock.patch.object(px, "build_hints", return_value=fake_hints):
            n = px._sync_finished([(1647, "Volga", "Volga"), (1648, "Tourte_voyageuse", "Tourte voyageuse"),
                                   (1650, "X", "X")], active_num=1650)
        self.assertEqual(n, 2)
        self.assertEqual(self.read("2026-11-15")["word"], "Volga")
        fixed = self.read("2026-11-16")
        self.assertEqual(fixed["word"], "Tourte voyageuse")
        self.assertEqual(fixed["generated_at"], "2026-11-16T11:05:00+00:00")   # heure d'origine gardée
        self.assertFalse((self.archive / "2026-11-18.json").exists())         # puzzle en cours ignoré


if __name__ == "__main__":
    unittest.main()
