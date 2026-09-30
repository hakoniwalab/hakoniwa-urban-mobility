# Urban Mobility root manifest

[`urban.manifest.yaml`](../urban.manifest.yaml), at the root of this
repository, is the one place that says what Urban Mobility is made of and
which ports it uses. Tools read it through
[`tools/urban_manifest.py`](../tools/urban_manifest.py) instead of spelling
paths and port numbers out; other tools in the workspace (an environment
authoring tool, a workspace port check) can read the same file.

```text
tools/urban_manifest.py ports [--json]   every port, its value and where the value comes from
tools/urban_manifest.py check            the manifest's files exist, ports are unique and clear of reserved ones
```

## 1. Paths

Paths are relative to this repository unless they start with a placeholder:
`${workspace}` (the folder holding this repository and its siblings),
`${business_pack}` (`${workspace}/hakoniwa-business-pack`) and `${work}`
(`${business_pack}/work`).

## 2. Parts

| Section | What it names | Read by |
|---|---|---|
| `contracts` | the contracts this repository publishes and their schema ids: the Asset / Composition contract, the City World job contract, this manifest | people and other tools |
| `assets` | where the Asset catalog is read (asset-contract section 3.1): this repository's `assets/` (recursive), other repositories' top-level `assets/`, the user Assets (registered Cities and Worlds), the City World Web UI's jobs; the manifest file suffix | `tools/urban_assets.py` |
| `compositions` | where example Compositions are read: this repository's `recipes/compositions/`, other repositories' top-level `assets/*.composition.yaml`; where Urban Studio saves Compositions. An example is listed only while every Asset it names is in the catalog | `tools/urban_studio.py` |
| `worlds` | the default World Asset of a Composition that selects none (asset-contract section 6.2; `check` finds it in the catalog) and the plain ground's World YAML | `tools/urban_simulation.py` (the plain ground) |
| `recipes` | the managed Recipe each route prepares its environment with (asset-contract section 2.1): path, Recipe id, use case | `tools/urban_simulation.py` |

## 3. Ports

Every TCP port Urban listens on or connects to has an id and a default:

| Id | Default | Protocol | Purpose |
|---|---|---|---|
| `urban-studio` | 8090 | http | the Urban Studio browser backend |
| `city-world-web-ui` | 8008 | http | the Business Pack City World Web UI Urban Studio links to (fixed) |
| `viewer-http` | 8000 | http | the workspace file server of a running simulation's viewers |
| `web-bridge` | 8765 | websocket | the standard WebBridge (integrated Car + Drone, the Business Pack fleet bridge) |
| `web-bridge-car` | 18765 | websocket | the WebBridge of a Car-only Composition |
| `web-bridge-fleet` | 18766 | websocket | the WebBridge of a Drone fleet Composition |
| `launcher-control` | 54111 | tcp | the Launcher's control port (fixed) |

A port's value is, highest first:

1. the Composition field or command-line option in its `set_by` (for one run:
   `viewer.http_port`, `viewer.web_bridge_port`, `--port`, `--web-bridge-port`);
2. the environment variable `HAKONIWA_URBAN_PORT_<ID>` (the id upper-cased,
   `-` as `_`; for example `HAKONIWA_URBAN_PORT_WEB_BRIDGE=28765`);
3. the machine's overrides file `${work}/urban/ports.yaml`
   (`ports: {web-bridge: 28765}`), for a machine where a default is taken
   (for example 8765 by a WSL port proxy);
4. the manifest's `default`.

A `fixed` port belongs to another component that cannot take a new one yet
(the Business Pack Launcher and City World Web UI); it is listed so nothing
else takes it, and overrides do not change it.

`reserved` lists the defaults of other tools that run in the same workspace
(Booth Studio 8096, Environment Studio 8097). `check` fails when an Urban
default equals one of them or another Urban default.
