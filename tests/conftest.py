from uuid import uuid4


def pytest_configure(config):
    """Give each test run an isolated temporary directory."""
    if config.option.basetemp is None:
        root = (config.rootpath / '.test-runs').resolve()
        root.mkdir(exist_ok=True)
        # Pytest clears an explicit basetemp. Use a new, dedicated child every time
        # so it never scans or clears previous runs or application data.
        target = root / f'run-{uuid4().hex}'
        if target.resolve().parent != root or target.exists():
            raise RuntimeError('Could not allocate an isolated test directory')
        config.option.basetemp = str(target)
