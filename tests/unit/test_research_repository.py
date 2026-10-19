"""Offline account, isolation, publication and reversible-preference tests."""
import json
import sqlite3

import pytest

from newspaper_agent.database.research_repository import ResearchRepository


@pytest.fixture
def repo(tmp_path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    repository.register("alice", "test-password-alice")
    repository.register("bob", "test-password-bob")
    return repository


def test_accounts_are_hashed_and_first_account_is_admin(repo):
    assert repo.authenticate("alice", "test-password-alice")
    assert not repo.authenticate("alice", "wrong-password")
    assert not repo.authenticate("unknown", "test-password-alice")
    assert not repo.authenticate("../../alice", "test-password-alice")
    assert repo.is_admin("alice")
    assert not repo.is_admin("bob")
    with sqlite3.connect(repo.path) as db:
        salt, digest, iterations = db.execute(
            "SELECT salt,password_hash,iterations FROM research_users WHERE id='alice'").fetchone()
    assert len(salt) == 16 and len(digest) == 32 and iterations >= 600_000
    assert b"test-password-alice" not in repo.path.read_bytes()
    with pytest.raises(ValueError, match="已存在"):
        repo.register("alice", "another-test-password")
    with pytest.raises(ValueError):
        repo.register("../outside", "another-test-password")
    with pytest.raises(ValueError):
        repo.register("charlie", "short")


def test_profile_feedback_and_undo_are_user_scoped(repo):
    original = repo.profile("bob")
    event = repo.feedback("bob", "missed", "遗漏了计算机视觉领域报道", {
        "interests": ["计算机视觉", "计算机视觉"], "domains": ["academic"], "style": "brief"})
    assert repo.profile("bob")["interests"] == ["计算机视觉"]
    assert repo.profile("bob")["version"] == 1
    assert repo.profile("alice")["interests"] == []
    assert repo.events("alice") == []
    assert repo.events("bob")[0]["changes"]["style"] == "brief"
    with pytest.raises(ValueError, match="没有可撤销"):
        repo.undo("alice", event)
    repo.undo("bob", event)
    restored = repo.profile("bob")
    assert restored["version"] == 2
    assert {k: v for k, v in restored.items() if k != "version"} == {
        k: v for k, v in original.items() if k != "version"}
    assert not repo.events("bob")[0]["active"]
    with pytest.raises(ValueError):
        repo.undo("bob", event)


def test_undo_does_not_overwrite_newer_preferences(repo):
    old = repo.update_profile("bob", {"interests": ["机器人"]}, "用户明确添加")
    new = repo.update_profile("bob", {"interests": ["机器人", "量子计算"]}, "用户明确扩展")
    with pytest.raises(ValueError, match="后续修改"):
        repo.undo("bob", old)
    assert repo.profile("bob")["interests"] == ["机器人", "量子计算"]
    repo.undo("bob", new)
    assert repo.profile("bob")["interests"] == ["机器人"]
    # Versions remain monotonic; historic edits cannot silently overwrite state.
    with pytest.raises(ValueError, match="后续修改"):
        repo.undo("bob", old)


def test_feedback_without_changes_is_reversible_and_not_a_truth_vote(repo):
    event = repo.feedback("bob", "fact_error", "原文数字可能有误", evidence_id="article:123")
    assert repo.profile("bob")["version"] == 0
    repo.update_profile("bob", {"style": "expert"}, "希望更多技术细节")
    repo.undo("bob", event)
    assert repo.profile("bob")["style"] == "expert"
    assert repo.events("bob")[1]["evidence_id"] == "article:123"


@pytest.mark.parametrize("changes", [
    {}, {"is_admin": True}, {"interests": "topic"}, {"interests": [1]},
    {"interests": ["x" * 121]}, {"domains": ["not_a_domain"]}, {"style": "unknown"},
    {"language": "unknown"}, {"version": 0}, {"excluded": ["x"] * 31},
])
def test_invalid_profile_changes_leave_no_event(tmp_path, changes):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    repo.register("alice", "test-only-password")
    with pytest.raises(ValueError):
        repo.update_profile("alice", changes, "测试拒绝非法变更")
    assert repo.events("alice") == []
    assert repo.profile("alice")["version"] == 0


def test_private_sources_are_hidden_even_from_admin(repo):
    item = repo.store_source("bob", "科研动态", "https://example.org/feed.xml", "academic")
    assert item["status"] == "discovered"
    assert repo.list_sources("alice", include_pending=True) == []
    assert repo.list_sources("bob")[0]["id"] == item["id"]
    with pytest.raises(ValueError, match="不可访问"):
        repo.source("alice", item["id"])
    with pytest.raises(ValueError):
        repo.set_source_status("alice", item["id"], "validated")
    with pytest.raises(ValueError):
        repo.set_source_status("bob", item["id"], "enabled")
    repo.set_source_status("bob", item["id"], "validated")
    repo.set_source_status("bob", item["id"], "enabled")
    assert repo.source("bob", item["id"])["status"] == "enabled"
    repo.set_source_status("bob", item["id"], "suspended")
    assert repo.source("bob", item["id"])["status"] == "suspended"
    with pytest.raises(ValueError, match="相同来源"):
        repo.store_source("bob", "duplicate", "https://example.org/feed.xml", "academic")
    # Another user's personal source is a separate record, not an existence leak.
    assert repo.store_source("alice", "my feed", "https://example.org/feed.xml", "news")["owner"] == "alice"


def test_shared_sources_require_admin_validation_then_explicit_publication(repo):
    original = repo.store_source("bob", "科研动态", "https://example.org/feed.xml", "academic")
    repo.update_profile("bob", {"interests": ["私密课题"]}, "私人偏好")
    shared = repo.share_source("bob", original["id"])
    assert shared["scope"] == "shared" and shared["status"] == "discovered"
    assert "私密课题" not in json.dumps(shared, ensure_ascii=False)
    assert repo.list_sources("alice") == []
    assert repo.list_sources("alice", include_pending=True)[0]["id"] == shared["id"]
    with pytest.raises(ValueError):
        repo.publish_source("bob", shared["id"])
    with pytest.raises(ValueError):
        repo.set_source_status("bob", shared["id"], "validated")
    with pytest.raises(ValueError, match="先通过验证"):
        repo.publish_source("alice", shared["id"])
    repo.set_source_status("alice", shared["id"], "validated")
    with pytest.raises(ValueError):
        repo.set_source_status("alice", shared["id"], "enabled")
    repo.publish_source("alice", shared["id"])
    assert repo.list_sources("alice")[0]["id"] == shared["id"]
    assert repo.source("bob", original["id"])["status"] == "discovered"
    with pytest.raises(ValueError):
        repo.set_source_status("bob", shared["id"], "archived")
    repo.set_source_status("alice", shared["id"], "suspended")
    assert repo.list_sources("alice") == []


def test_contributor_can_withdraw_unpublished_shared_candidate(repo):
    item = repo.store_source("bob", "候选", "https://example.org/rss", "news", scope="shared")
    repo.set_source_status("bob", item["id"], "archived")
    assert repo.source("bob", item["id"])["status"] == "archived"
    with pytest.raises(ValueError):
        repo.publish_source("alice", item["id"])
    with pytest.raises(ValueError):
        repo.set_source_status("alice", item["id"], "validated")


def test_shared_candidate_hidden_from_unrelated_user(repo):
    repo.register("charlie", "test-password-charlie")
    shared = repo.store_source("bob", "candidate", "https://example.org/feed", "general", scope="shared")
    assert repo.list_sources("charlie", include_pending=True) == []
    with pytest.raises(ValueError, match="不可访问"):
        repo.source("charlie", shared["id"])
    repo.set_source_status("alice", shared["id"], "validated")
    repo.publish_source("alice", shared["id"])
    assert repo.source("charlie", shared["id"])["status"] == "enabled"
    with pytest.raises(ValueError):
        repo.share_source("charlie", shared["id"])


def test_invalid_source_registration_cannot_bypass_validation(repo):
    for kwargs in ({"url": "https://127.0.0.1/feed"}, {"url": "https://example.org/feed?token=private"},
                   {"adapter": "arbitrary.python.Module"}, {"status": "enabled"}, {"scope": "all"}):
        args = {"name": "news", "url": "https://example.org/feed", "domain": "news", **kwargs}
        with pytest.raises(ValueError):
            repo.store_source("bob", **args)
    assert repo.list_sources("bob") == []
    with pytest.raises(ValueError):
        repo.set_source_status("bob", [], "validated")
    with pytest.raises(ValueError):
        repo.set_source_status("bob", "anything", [])
    with pytest.raises(ValueError):
        repo.feedback("bob", [], "invalid feedback")


def test_reports_are_private_and_bounded(repo):
    report_id = repo.save_report("bob", "我的研究问题", {"answer": "内容", "evidence": [{"id": "paper:1"}]})
    assert repo.reports("bob")[0]["id"] == report_id
    assert repo.report("bob", report_id)["report"]["answer"] == "内容"
    assert repo.reports("alice") == []
    with pytest.raises(ValueError, match="不可访问"):
        repo.report("alice", report_id)
    with pytest.raises(ValueError):
        repo.save_report("bob", "问题", {"nonfinite": float("nan")})
    with pytest.raises(ValueError):
        repo.save_report("bob", "问题", {"large": "x" * 1_000_001})
    with pytest.raises(ValueError):
        repo.reports("bob", limit=True)
    with pytest.raises(ValueError):
        repo.profile("unknown")
