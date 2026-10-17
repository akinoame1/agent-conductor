"""conductor: managing an AI agent's work so it cooperates — the queue, what is added to a message on its way,
keep-going wakes, and the harness adapters. No dependencies beyond the Python standard library; a host supplies storage, a transport to the
harness, and its own policy (see engine.py)."""
