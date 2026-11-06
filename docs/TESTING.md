# Testing

All tests use isolated temporary databases; the installed Memory OS database is never touched.

```bash
cd ~/Developer/ai-model-memory-
python3 -m unittest discover -s tests          # everything (81 tests)
python3 engine/memoryos.py test --unit         # engine + memory tools
python3 engine/memoryos.py test --integration
python3 engine/memoryos.py test --mcp          # real MCP protocol round-trips
python3 engine/memoryos.py test --storage      # storage engine tests
python3 engine/memoryos.py test --r2           # R2: config, envelope, SigV4, sync, dry-run
python3 engine/memoryos.py test --regression   # SQLite + backup regression
python3 engine/memoryos.py doctor              # read-only health report
python3 engine/memoryos.py doctor-json         # machine-readable report
```

Suites: `test_engine.py` + `test_backup.py` (legacy regression), `test_memory_tools.py` (engine surface incl. FTS migration + MCP round-trips), `test_v04_memory.py` (lifecycle intelligence), `test_v04_storage.py` (R2), `test_v04_security.py` (redaction, traversal, malicious content, isolation, concurrency). Backend FastAPI tests: `backend/.venv/bin/python -m pytest backend/tests` against the isolated `memoryos_test` database.
