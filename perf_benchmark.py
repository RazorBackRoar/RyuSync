import time
import re
from pathlib import Path

def extract_game_id(filename):
    id_match = re.search(r"\[(01[0-9A-Fa-f]{14,16})\]", filename, re.IGNORECASE)
    if id_match:
        return id_match.group(1).upper()
    return None

def get_base_id(full_game_id):
    if full_game_id and len(full_game_id) >= 12 and full_game_id.startswith("01"):
        return full_game_id[:12].upper()
    return None

def is_same_game(name1, name2):
    id1_full = extract_game_id(name1)
    id2_full = extract_game_id(name2)
    base_id1 = get_base_id(id1_full)
    base_id2 = get_base_id(id2_full)

    if base_id1 and base_id2:
        return base_id1 == base_id2
    return False

class MockPath:
    def __init__(self, name):
        self.name = name

# Generate mock data
folder1_files = [MockPath(f"Game1_v{i}_[0100A77018EA0000].nsp") for i in range(100)]
folder2_files = [MockPath(f"Game2_v{i}_[0100B88019FA0000].nsp") for i in range(100)]

# Append match at the end
folder1_files.append(MockPath("Match_[0100C9901A0A0000].nsp"))
folder2_files.append(MockPath("Match_[0100C9901A0A0000].nsp"))

start = time.time()
for _ in range(100):
    match_found = False
    for file1 in folder1_files:
        for file2 in folder2_files:
            if is_same_game(file1.name, file2.name):
                match_found = True
                break
        if match_found:
            break
baseline = time.time() - start

start = time.time()
for _ in range(100):
    match_found = False

    # Pre-computing
    folder1_base_ids = [get_base_id(extract_game_id(f.name)) for f in folder1_files]
    folder2_base_ids = [get_base_id(extract_game_id(f.name)) for f in folder2_files]

    for b1 in folder1_base_ids:
        for b2 in folder2_base_ids:
            if b1 and b2 and b1 == b2:
                match_found = True
                break
        if match_found:
            break
optimized = time.time() - start

print(f"Baseline: {baseline:.4f}s")
print(f"Optimized: {optimized:.4f}s")
print(f"Improvement: {baseline / optimized:.2f}x")
