import timeit
import re

setup = """
from ryusync.main import extract_game_id
filenames = [
    "Super Mario Odyssey [0100000000010000][v0].nsp",
    "The Legend of Zelda: Breath of the Wild [01007EF00011E000][v196608].xci",
    "Animal Crossing: New Horizons [01002810058EB000].nsp",
    "Some Game Without ID.nsp",
    "Game with wrong ID [0200000000010000].nsp"
] * 1000
"""

stmt = """
for f in filenames:
    extract_game_id(f)
"""

print("Baseline performance:")
print(timeit.timeit(stmt, setup=setup, number=100))
