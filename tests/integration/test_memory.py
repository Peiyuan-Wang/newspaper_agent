from newspaper_agent.database.connection import connect_sqlite
from newspaper_agent.database.repositories import SQLiteMemoryRepository
from newspaper_agent.memory.long_term import LongTermMemory


def test_long_term_memory_round_trip(tmp_path):
    repository = SQLiteMemoryRepository(connect_sqlite(tmp_path / "memory.db"))
    memory = LongTermMemory(repository)
    memory.remember("topic", "news")
    assert memory.recall("topic") == "news"
