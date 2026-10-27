"""Тесты eval/run.py без обращения к LLM."""
import json

from app.llm import LLMClient, Reply
from eval.run import CaseOutcome, CaseResult, Criterion, failed_texts, judge, summary


class FakeJudge(LLMClient):
    def __init__(self, answers):
        super().__init__("judge", 0.0, 100)
        self.answers = list(answers)

    def chat(self, messages, tools=None, json_mode=False):
        assert json_mode
        return Reply(text=self.answers.pop(0))


def criteria():
    return [Criterion("M1", "must", "представляется"), Criterion("N1", "must_not", "два вопроса")]


def test_judge_fills_verdicts():
    cs = criteria()
    answer = {"results": [{"id": "M1", "pass": True, "reason": "ок"}, {"id": "N1", "pass": False, "reason": "два ?"}]}
    judge(FakeJudge([json.dumps(answer)]), "<knowledge></knowledge>", [("Пользователь", "Привет")], cs)
    assert [(c.passed, c.reason) for c in cs] == [(True, "ок"), (False, "два ?")]


def test_judge_retries_bad_json_and_fails_missing():
    cs = criteria()
    good = json.dumps({"results": [{"id": "M1", "pass": True, "reason": "ок"}]})
    judge(FakeJudge(["не json", good]), "", [], cs)
    assert cs[0].passed is True
    assert cs[1].passed is False and "не оценил" in cs[1].reason


def test_summary_requires_strict_types():
    ok = CaseResult("S01", "typical", [], criteria=[Criterion("M1", "must", "x", passed=True)])
    bad_pd = CaseResult("S05", "pd", [], criteria=[Criterion("M1", "must", "x", passed=False)])
    results = [ok] * 10 + [bad_pd]  # 90.9 %, но провален pd
    s = summary(results)
    assert s["percent"] >= 90 and s["strict_ok"] is False and s["accepted"] is False
    assert summary([ok] * 10)["accepted"] is True


def test_error_case_fails():
    r = CaseResult("S01", "typical", [], criteria=[Criterion("M1", "must", "x", passed=True)], error="сеть")
    assert r.passed is False


def attempt(ok):
    return CaseResult("S04", "typical", [], criteria=[Criterion("M1", "must", "брокерский отдел", passed=ok)])


def test_repeat_passes_only_if_all_runs_pass():
    flaky = CaseOutcome("S04", "typical", [attempt(True), attempt(False), attempt(True)])
    assert not flaky.passed and flaky.runs_passed == 2
    assert flaky.shown is flaky.attempts[1]  # в отчет идет проваленный прогон
    assert failed_texts(flaky) == "брокерский отдел (1/3)"
    assert CaseOutcome("S04", "typical", [attempt(True)] * 3).passed


def test_summary_counts_outcomes():
    s = summary([CaseOutcome("S01", "pd", [attempt(True), attempt(False)])])
    assert s["cases"] == 1 and s["cases_passed"] == 0 and s["criteria"] == 2 and not s["strict_ok"]
