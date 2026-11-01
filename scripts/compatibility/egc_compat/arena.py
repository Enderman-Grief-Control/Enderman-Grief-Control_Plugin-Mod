"""The controlled arena, shared by dedicated-server rows (built over RCON) and client rows (built
by a data pack), so both sides judge pickup and placement in the same place.

Everything sits inside force-loaded chunks (0,0) and (1,0) of a flat world, far above the natural
surface. A glass shell keeps the Enderman inside, blocks sky light, and leaves grass as the only
holdable block it can reach. An identical copy built REFERENCE_DY higher lets one
`execute if blocks` query prove that nothing in the arena changed.
"""

from __future__ import annotations

ARENA_MIN = (4, 99, 4)
ARENA_MAX = (12, 105, 12)
REFERENCE_DY = 12
REFERENCE_MIN = (ARENA_MIN[0], ARENA_MIN[1] + REFERENCE_DY, ARENA_MIN[2])
REFERENCE_MAX = (ARENA_MAX[0], ARENA_MAX[1] + REFERENCE_DY, ARENA_MAX[2])
# Blocks compared by one `execute if blocks` query: 9 x 7 x 9.
ARENA_BLOCKS = (ARENA_MAX[0] - ARENA_MIN[0] + 1) * (ARENA_MAX[1] - ARENA_MIN[1] + 1) * (ARENA_MAX[2] - ARENA_MIN[2] + 1)
ARENA_SPAWN = "8.5 100 8.5"
HOLDER_BLOCK = (8, 100, 8)
GRASS = ((6, 100, 6), (8, 100, 6), (10, 100, 6), (6, 100, 8),
         (10, 100, 8), (6, 100, 10), (8, 100, 10), (10, 100, 10))
SHEEP_MIN = (18, 98, 6)
SHEEP_MAX = (22, 102, 10)
SHEEP_GRASS = tuple((x, 99, z) for x in (19, 20, 21) for z in (7, 8, 9))
SHEEP_SPAWN = "20.5 100 8.5"
CLIENT_SPOT = "14.5 102 8.5"
FORCELOAD = "forceload add 0 0 31 15"
WORLD_TIME = 18000  # midnight: Endermen do not teleport away from the arena at night
# Pieces of glass the client player stands on, outside the arena wall, facing in.
CLIENT_PLATFORM = (14, 101, 8)
CLIENT_VIEW = "14.5 102 8.5 90 20"


def in_box(coords: tuple[int, int, int], low: tuple[int, int, int], high: tuple[int, int, int]) -> bool:
    return all(lo <= value <= hi for value, lo, hi in zip(coords, low, high))


def in_arena(coords: tuple[int, int, int]) -> bool:
    return in_box(coords, ARENA_MIN, ARENA_MAX)


def box(low: tuple[int, int, int], high: tuple[int, int, int]) -> str:
    return " ".join(str(value) for value in (*low, *high))


def pos(coords: tuple[int, int, int]) -> str:
    return " ".join(str(value) for value in coords)
