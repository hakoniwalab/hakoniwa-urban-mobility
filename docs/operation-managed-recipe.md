# Managed Urban Mobility Recipe operation

This document defines the standard lifecycle for the integrated Urban Mobility
Recipe. The Business Pack Recipe engine owns dependency and Foundation
validation; Urban-specific tools own composition and runtime materialization.

The standard entrypoint is:

```text
tools/urban_mobility.py
```

Do not use `tools/multi_car.py doctor` as the first Windows health check.
That command is a lower-level one-Car regression tool.

## Windows 11 / PowerShell

The expected sibling layout is:

```text
C:\project\urban\
  hakoniwa-business-pack\
  hakoniwa-urban-mobility\
  hakoniwa-drone-core\
  hakoniwa-drone-show\
  hakoniwa-robot-runtime\
  hakoniwa-mujoco-robots\
  hakoniwa-mbody-registry\
  hakoniwa-threejs-drone\
  hakoniwa-map-viewer\
```

After Business Pack setup or portable workspace installation, enter the managed
workspace:

```powershell
PS C:\project\urban> cd .\hakoniwa-business-pack
PS C:\project\urban\hakoniwa-business-pack> python .\tools\workspace.py enter
Entering Hakoniwa Workspace Environment: C:\project\urban\hakoniwa-business-pack
(hako) PS C:\project\urban\hakoniwa-business-pack> cd ..\hakoniwa-urban-mobility
```

Confirm that `python` resolves to the Foundation Python selected by the
Business Pack workspace:

```powershell
python -c "import sys; print(sys.executable)"
```

A normal development workspace uses
`work\foundation\install\python\Scripts\python.exe` on Windows.

## 1. Plan

```powershell
python .\tools\urban_mobility.py plan
```

`plan` resolves the managed Recipe, local source requirements, Foundation
requirements, and intended actions. It must not run the Urban simulation.

## 2. Doctor

```powershell
python .\tools\urban_mobility.py doctor
```

`doctor` delegates to the Business Pack Recipe engine. It checks the managed
Recipe contract, installed Foundation receipts/capabilities, and declared local
artifacts before Urban-specific composition begins.

All external Urban component repositories are declared as Git sources. If a
sibling checkout is absent, `plan` reports `clone` and `configure` can
materialize it automatically. Existing sibling checkouts are reused. The
`HAKONIWA_*_ROOT` overrides remain available for explicitly selected local
checkouts.

The managed `plan` and `doctor` validate source prerequisites, not generated
build outputs. In particular, the Car plant executable is produced by
`configure`; requiring `build/bin/urban-car-hakoniwa-asset.exe` before
`configure` would create a circular prerequisite.

Do not install ad-hoc Python packages or manually build generated outputs merely
to satisfy a lower-level tool before this managed `doctor` has passed.

## 3. Configure

```powershell
python .\tools\urban_mobility.py configure
```

`configure` first runs the Business Pack Recipe configure path, then
materializes the Urban integrated composition under:

```text
hakoniwa-business-pack\work\recipes\urban-mobility-rc\
```

The managed Recipe declares
`recipes/requirements/urban-mobility-rc.txt` as its Python runtime
requirements. Business Pack installs those requirements into Foundation Python
before Urban-specific composition runs; currently this supplies
`PyYAML>=6.0,<7`. Do not repair a missing module with an ad-hoc
`pip install`; fix the managed Recipe dependency contract instead.

## 4. Start and inspect

```powershell
python .\tools\urban_mobility.py start
python .\tools\urban_mobility.py status
python .\tools\urban_mobility.py open-viewer
```

`start` re-runs managed Recipe `doctor` before launching. Runtime control
uses the Foundation Python selected by the Business Pack platform layout rather
than a hard-coded POSIX `python/bin/python3` path.

## 5. Stop

```powershell
python .\tools\urban_mobility.py stop
python .\tools\urban_mobility.py status
```

Only the selected `urban-mobility-rc` Launcher session is controlled.

## macOS / Linux

Use the same Urban commands. Activate or enter the Business Pack workspace
first, then run:

```bash
python tools/urban_mobility.py plan
python tools/urban_mobility.py doctor
python tools/urban_mobility.py configure
python tools/urban_mobility.py start
```

The Business Pack workspace selects the native Foundation Python layout for the
host platform.

## Lower-level regression tools

The following tools remain useful but are not the primary managed Recipe
entrypoint:

- `tools/multi_car.py`: Car fleet composition and one-Car/multi-Car regression.
- `tools/drone_one.py`: one-Drone component regression.
- `tools/drone_car_rc.py`: legacy two-asset RC regression.
- `tools/urban_composer.py`: integrated composition implementation used by
  `urban_mobility.py configure`.

When a managed `doctor` failure points to one of these components, use its
lower-level diagnostics to isolate the component. Do not invert that order for
normal setup verification.
