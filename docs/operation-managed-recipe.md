# Managed Urban Mobility Recipe operation

`tools/urban_mobility.py` is the standard lifecycle entrypoint. A managed
Recipe selects the simulation topology; configure-time arguments fill the
user-specific inputs such as the City World receipt.

The Python entrypoint is intentionally thin:

```text
urban_mobility.py
  -> selected managed Recipe
  -> Business Pack plan / doctor / configure
  -> use-case template materialization
  -> component-owned configure/runtime tools
```

Do not use `tools/multi_car.py doctor` as the first setup health check. It is
a lower-level Car regression tool.

## Windows 11 / PowerShell

After Business Pack setup, enter its workspace and return to Urban Mobility:

```powershell
PS C:\project\urban> cd .\hakoniwa-business-pack
PS C:\project\urban\hakoniwa-business-pack> python .\tools\workspace.py enter
(hako) PS C:\project\urban\hakoniwa-business-pack> cd ..\hakoniwa-urban-mobility
```

Confirm the selected Foundation Python if needed:

```powershell
python -c "import sys; print(sys.executable)"
```

On a normal Windows development workspace it resolves below
`work\foundation\install\python\Scripts\python.exe`.

## Car + RC use case

The first managed template Recipe is:

```text
recipes/usecases/urban-car-rc.yaml
```

It owns the reusable topology:

- one Ackermann Golf Cart,
- one RC command producer,
- one Car physics plant,
- one WebBridge and browser Viewer,
- the Foundation and Git dependencies needed to run them.

The City World is not part of that topology. Pass an existing
`city-world-receipt.json` produced by the City World workflow.

### 1. Plan

```powershell
python .\tools\urban_mobility.py plan `
  --recipe recipes\usecases\urban-car-rc.yaml
```

Missing public sibling repositories are planned as Git clones. Existing
checkouts are reused.

### 2. Doctor

```powershell
python .\tools\urban_mobility.py doctor `
  --recipe recipes\usecases\urban-car-rc.yaml
```

Doctor checks the selected Recipe contract, Foundation receipts/capabilities,
and source prerequisites without building generated Urban outputs.

### 3. Configure with a City World receipt

```powershell
python .\tools\urban_mobility.py configure `
  --recipe recipes\usecases\urban-car-rc.yaml `
  --city-receipt C:\path\to\city-world-receipt.json
```

The Recipe declares this parameter mapping:

```yaml
urban_mobility:
  use_case: car-rc
  template:
    path: recipes/experiments/urban-car-one.yaml
  parameters:
    city_receipt:
      cli: --city-receipt
      target: inputs.business_pack_city_receipt.path
      type: path
      required: true
```

The Car RC template uses WebBridge port `18765` by default so it does not
collide with Windows IP Helper configurations that may own `8765`. Override it
when necessary:

```powershell
python .\tools\urban_mobility.py configure `
  --recipe recipes\usecases\urban-car-rc.yaml `
  --city-receipt C:\path\to\city-world-receipt.json `
  --web-bridge-port 19001
```

The tracked template is not edited. Urban writes an effective generated
composition under:

```text
hakoniwa-business-pack\work\recipes\urban-car-rc\config\urban-composition.json
```

Business Pack installs the Recipe-owned Python requirements
(`PyYAML` and `pygame`) into Foundation Python before the Urban composition
is materialized.

### 4. Check the RC controller

```powershell
python .\tools\urban_mobility.py check-rc `
  --recipe recipes\usecases\urban-car-rc.yaml
```

The tracked DualSense axis contract is platform-neutral. Actual controller
enumeration and axis behavior should still be verified on each host platform.

### 5. Start, inspect, and stop

```powershell
python .\tools\urban_mobility.py start `
  --recipe recipes\usecases\urban-car-rc.yaml
python .\tools\urban_mobility.py status `
  --recipe recipes\usecases\urban-car-rc.yaml
python .\tools\urban_mobility.py open-viewer `
  --recipe recipes\usecases\urban-car-rc.yaml
python .\tools\urban_mobility.py stop `
  --recipe recipes\usecases\urban-car-rc.yaml
```

Runtime lifecycle state is isolated under the selected Recipe ID
(`urban-car-rc`).

## macOS / Linux

Use the same Recipe and arguments after entering/activating the Business Pack
workspace:

```bash
python tools/urban_mobility.py plan \
  --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py doctor \
  --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py configure \
  --recipe recipes/usecases/urban-car-rc.yaml \
  --city-receipt /path/to/city-world-receipt.json
```

Business Pack resolves the host-native Foundation Python and executable layout.

## Other managed use cases

The existing integrated Drone + multi-Car Recipe is still available:

```text
recipes/experiments/urban-mobility-rc.yaml
```

It declares `use_case: drone-car-distributed` and points to its tracked
Shizuoka composition. This is retained while the generalized Recipe model is
introduced. Future Recipes can describe shared-world Car/Drone simulation,
Drone Show, or other topologies.

## Lower-level regression tools

- `tools/multi_car.py`: Car fleet composition and one-Car/multi-Car regression.
- `tools/drone_one.py`: one-Drone component regression.
- `tools/drone_car_rc.py`: legacy two-asset RC regression.
- `tools/urban_composer.py`: integrated Drone + multi-Car composition.

Use lower-level diagnostics after the managed Recipe identifies a component
problem; do not make them the normal setup entrypoint.
