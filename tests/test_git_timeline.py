"""Tests for git_timeline module"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from git_timeline import GitTimelineWasher

def test_noise_filter():
    """Verify noise pattern matching works"""
    washer = GitTimelineWasher()
    
    noise_messages = [
        "format code with prettier",
        "fix typo in comment",
        "Merge branch 'feature-x' into main",
        "bump version to 2.0.1",
        "update dependencies",
        "clean up whitespace",
    ]
    
    signal_messages = [
        "feat: add user registration endpoint",
        "fix: prevent deadlock in order processing JIRA-1234",
        "refactor: extract payment service from order handler",
        "perf: optimize inventory query with caching",
        "security: patch SQL injection vulnerability",
        "BREAKING: change API response format for v2",
    ]
    
    for msg in noise_messages:
        assert washer._is_noise(msg), f"Should be noise: {msg}"
    
    for msg in signal_messages:
        assert not washer._is_noise(msg), f"Should be signal: {msg}"

def test_time_decay():
    washer = GitTimelineWasher()
    # A commit from 365 days ago should have lower weight than 30 days ago
    old_weight = washer.compute_time_decay_weight("2025-06-10", "2026-06-10")
    recent_weight = washer.compute_time_decay_weight("2026-05-10", "2026-06-10")
    assert recent_weight > old_weight, f"Recent weight {recent_weight} should be > old weight {old_weight}"

if __name__ == "__main__":
    test_noise_filter()
    test_time_decay()
    print("All tests passed")
