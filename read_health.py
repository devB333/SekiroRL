"""Read-only Sekiro 1.06 candidate HP reader. See MEMORY_READER.md.

The implementation lives in sekirorl/memory.py; this file keeps the documented
command line (`python .\\read_health.py --pid ...`) working.
"""
from sekirorl.memory import Reader, Unavailable, main  # noqa: F401

if __name__ == "__main__":
    main()
