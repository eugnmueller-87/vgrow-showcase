"""Hank v1 eval set (docs/hank-v1-spec.md section 11): cases, expectations, fixtures, a fake Ollama, scoring, report.

cases.json holds the questions with must_mention/must_not, expected.json what counts as right today (status, route,
passage ids for recall@3). fixtures.py builds the live state F22 and F22-alt, fake_ollama.py stands in for Ollama,
score.py grades one answer, report.py runs the set and prints the table. Nothing here imports vgrow.hank at import
time: the set runs (and reports "not built") before the pipeline exists.
"""
