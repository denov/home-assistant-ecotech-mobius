# Contributing

Thanks for contributing!

## License of contributions (important)
By submitting a contribution (pull request, patch, etc.), you agree that:

1) Your contribution is licensed under **AGPL-3.0-or-later**; AND  
2) You grant the project maintainer (denov) a perpetual, worldwide, royalty-free
   right to use, modify, sublicense, and relicense your contribution under
   different terms, including a commercial license.

If you do not agree, please do not submit a contribution.

## Developer Certificate of Origin
You certify that you have the right to submit the work under the terms above.

## Development

Changes follow the spec, then the tests, then the code:

1. Update the spec in `docs/` first: `01`–`02` for the protocol, `03` for how the integration behaves.
2. Write a test that fails without the change.
3. Make it pass, keeping the code simple.

Use **Python 3.14**, as CI does. On 3.13 the test requirements resolve to an older Home Assistant that also needs `pyserial`.

```sh
uv venv --python 3.14 .venv && source .venv/bin/activate   # or: python3.14 -m venv .venv
pip install -r requirements_test.txt ruff==0.14.0
ruff check .            # lint, as CI runs it
python -m pytest        # tests
```

`tools/mobius_cli.py` talks to devices through an ESPHome proxy without Home Assistant (`pip install bleak-esphome habluetooth`). It's useful for trying a new device or firmware.
