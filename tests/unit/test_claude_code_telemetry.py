from __future__ import annotations

from app.modules.claude_telemetry.service import parse_claude_code_prometheus


def test_parse_claude_code_prometheus_aggregates_expected_metrics() -> None:
    payload = """
# HELP ignored ignored
claude_code_session_count_total 4
claude_code_cost_usage_total 3.5
claude_code_token_usage_total{type="input"} 100
claude_code_token_usage_total{type="output"} 20
claude_code_token_usage_total{type="cacheRead"} 10
claude_code_token_usage_total{type="cacheCreation"} 5
claude_code_active_time_total{type="user"} 120
claude_code_active_time_total{type="cli"} 300
claude_code_lines_of_code_count_total{type="added"} 50
claude_code_lines_of_code_count_total{type="removed"} 12
claude_code_commit_count_total 7
claude_code_pull_request_count_total 2
"""

    snapshot = parse_claude_code_prometheus(payload)

    assert snapshot.sessions_count == 4
    assert snapshot.cost_usage_usd == 3.5
    assert snapshot.token_input == 100
    assert snapshot.token_output == 20
    assert snapshot.token_cache_read == 10
    assert snapshot.token_cache_creation == 5
    assert snapshot.active_time_user_seconds == 120
    assert snapshot.active_time_cli_seconds == 300
    assert snapshot.lines_added == 50
    assert snapshot.lines_removed == 12
    assert snapshot.commits_count == 7
    assert snapshot.pull_requests_count == 2
