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

| Placeholder | Resolves to |
|---|---|
| `${repo}` | this repository |
| `${workspace}` | the folder holding this repository and its sibling repositories |
| `${business_pack}` | `$HAKONIWA_WORKSPACE_ROOT`, the Business Pack root |
| `${work}` | `$HAKONIWA_WORK_DIR`, the Business Pack work directory |

Urban's tools run in the Hakoniwa Business Pack Workspace
(hakoniwa-business-pack `docs/hakoniwa-workspace-environment-ja.md`). Entering
it (`python tools/workspace.py enter`) or running a command in it
(`python tools/workspace.py run -- <command>`) exports
`HAKONIWA_WORKSPACE_ACTIVE=1`, `HAKONIWA_WORKSPACE_ROOT` and
`HAKONIWA_WORK_DIR`. `${business_pack}` and `${work}` come only from those
variables. They are never guessed from the folder layout, so a relocated work
directory (`workspace.py enter --workdir ...`) is followed as it is.
`${work}` is `$HAKONIWA_WORK_DIR`, not `${business_pack}/work`.

Outside the Workspace (`HAKONIWA_WORKSPACE_ACTIVE` is not `1`, or the variable
is empty), resolving `${business_pack}` or `${work}` stops the tool with a
message that names `python tools/workspace.py enter` and a non-zero exit, not
a traceback (`urban_manifest.WorkspaceError`). Paths that use only `${repo}`
or `${workspace}` resolve without the Workspace.
Tools that need the Business Pack root or its work directory in code use
`urban_manifest.business_pack()` and `urban_manifest.work_dir()`, which follow
the same rule. A portable Urban package (`tools/portable_urban_car.py`) sets
the same three variables from its own layout.

## 2. Parts

| Section | What it names | Read by |
|---|---|---|
| `contracts` | the contracts this repository publishes and their schema ids: the Asset / Composition contract, the City World job contract, this manifest | people and other tools |
| `assets` | where the Asset catalog is read (asset-contract section 3.1): this repository's `assets/` (recursive), other repositories' top-level `assets/`, the user Assets (registered Cities and Worlds), the City World Web UI's jobs; the manifest file suffix | `tools/urban_assets.py` |
| `compositions` | where example Compositions are read: this repository's `recipes/compositions/`, other repositories' top-level `assets/*.composition.yaml`; where Urban Studio saves Compositions. An example is listed only while every Asset it names is in the catalog | `tools/urban_studio.py` |
| `studio` | where a background Urban Studio (`urban_studio.py start`) keeps its pid, port, and log | `tools/urban_studio.py` |
| `worlds` | the default World Asset of a Composition that selects none (asset-contract section 6.2; `check` finds it in the catalog) and the plain ground's World YAML | `tools/urban_simulation.py` (the plain ground) |
| `recipes` | the managed Recipe each route (car, integrated, drone, fleet, fpv) prepares its environment with (asset-contract section 2.1): path, Recipe id, use case | `tools/urban_simulation.py` |

## 3. Ports

Every TCP port Urban listens on or connects to has an id and a default. The
defaults are uncommon ports below the OS ephemeral ranges (Linux 32768+,
macOS/Windows 49152+), clear of common services (8000, 8080, 8765:
development servers, Docker containers, WSL port proxies), as
hakoniwa-fpv-drone's 28000 / 28765:

| Id | Default | Protocol | Purpose |
|---|---|---|---|
| `urban-studio` | 28090 | http | the Urban Studio browser backend |
| `environment-studio` | 28097 | http | the Environment Studio that makes and registers Cities; Urban Studio links to it (fixed) |
| `viewer-http` | 28100 | http | the workspace file server of a running simulation's viewers |
| `web-bridge` | 28865 | websocket | the standard WebBridge (integrated Car + Drone, the Business Pack fleet bridge) |
| `web-bridge-car` | 28866 | websocket | the WebBridge of a Car-only Composition |
| `web-bridge-fleet` | 28867 | websocket | the WebBridge of a Drone fleet Composition |

A port's value is, highest first:

1. the Composition field or command-line option in its `set_by` (for one run:
   `viewer.http_port`, `viewer.web_bridge_port`, `--port`, `--web-bridge-port`);
2. the environment variable `HAKONIWA_URBAN_PORT_<ID>` (the id upper-cased,
   `-` as `_`; for example `HAKONIWA_URBAN_PORT_WEB_BRIDGE=29865`);
3. the machine's overrides file `${work}/urban/ports.yaml`
   (`ports: {web-bridge: 29865}`), for a machine where a default is taken;
4. the manifest's `default`.

A `fixed` port belongs to another component that cannot take a new one yet
(Environment Studio); it is listed so nothing else takes it,
and overrides do not change it. The Launcher's control endpoint is not listed:
it binds a free port itself and records it in its session.

`reserved` lists the defaults of other tools that run in the same workspace
(the FPV viewer 28000 / 28765, Booth Studio 28096). `check` fails when an Urban
default equals one of them or another Urban default.
