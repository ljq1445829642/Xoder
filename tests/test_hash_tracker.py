"""Tests for hash_tracker module"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from hash_tracker import HashTracker

def test_compute_file_hash():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("def hello(): return 'world'\n")
        path = f.name
    
    try:
        tracker = HashTracker()
        h1 = tracker.compute_file_hash(path)
        assert len(h1) == 64  # SHA-256 hex
        h2 = tracker.compute_file_hash(path)
        assert h1 == h2  # Same content = same hash
    finally:
        os.unlink(path)

def test_compute_combined_hash():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f1, \
         tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f2:
        f1.write("a")
        f2.write("b")
        p1, p2 = f1.name, f2.name
    
    try:
        tracker = HashTracker()
        h = tracker.compute_combined_hash([p1, p2])
        assert len(h) == 64
    finally:
        os.unlink(p1)
        os.unlink(p2)

if __name__ == "__main__":
    test_compute_file_hash()
    test_compute_combined_hash()
    print("All tests passed")
