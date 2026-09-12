# Development and testing

Run from the repository root:

```bash
mise install          # installs the pinned tools and supported Python versions
tests/run.sh          # checks that don't require a graphical session
tests/run.sh --gates  # also checks lint gates against seeded violations
```

`tests/run.sh --live-harness` runs the Quickshell harness and needs a live
Wayland session. Run it before submitting QML changes. `--live-staged` requires
explicit authorisation because it installs into `~/.config/omarchy/plugins/`.

CI runs `tests/run.sh --gates` on Ubuntu. It cannot run `omarchy plugin validate`,
the `qmllint` gate or the Quickshell harness there. These checks are reported as
skipped, and the result is `SUITE PASS (PARTIAL)`. A passing CI run does not
replace the local QML checks.

The pre-release has been exercised against a UDM Pro on Network 10.6.101. A
full Devices and Clients batch, including per-device details, completed in
under half a second against a 25-second budget on that controller. This is a
measurement from that setup, not a performance guarantee.

## Project references

- [Feature specifications and verified host/API contracts](feature-specs/omarchy-unifi-plugin/)
- [Helper envelope protocol](protocol-v1.md)
- [Glossary](glossary.md) for deviation, amendment, phase, checkpoint, gate and risk identifiers
