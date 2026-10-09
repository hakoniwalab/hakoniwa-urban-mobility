#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: tools/px4_sitl_build.bash [--px4-dir DIR] [--out DIR]

Build PX4 SITL (px4_sitl_default) out of tree. The PX4-Autopilot checkout
(a git submodule) is only read: the Python environment, the build and the
compiler cache all go under the output directory. PX4's CMake always writes
two VS Code files into .vscode/; the script puts them back as they were. It
then compares the checkout's git status, including ignored files, before and
after the build and fails if anything changed.

Options:
  --px4-dir DIR  PX4-Autopilot checkout
                 (default: $PX4_AUTOPILOT_ROOT, else ../PX4-Autopilot)
  --out DIR      output directory (default: build/px4-sitl)

Results:
  <out>/build/px4_sitl_default/bin/px4   PX4 SITL binary
  <out>/build/px4_sitl_default/etc       PX4 startup scripts and airframes
  <out>/venv                             Python environment for the build

Environment:
  PX4_SITL_PYTHON  Python used to create the environment (default: python3)
EOF
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PX4_DIR_INPUT="${PX4_AUTOPILOT_ROOT:-${REPO_ROOT}/../PX4-Autopilot}"
OUT_DIR_INPUT="${REPO_ROOT}/build/px4-sitl"
CONFIG="px4_sitl_default"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --px4-dir) PX4_DIR_INPUT="$2"; shift 2 ;;
    --out) OUT_DIR_INPUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "${PX4_DIR_INPUT}/CMakeLists.txt" ]]; then
  echo "ERROR: PX4-Autopilot checkout was not found: ${PX4_DIR_INPUT}" >&2
  echo "Get it with: git clone --recursive https://github.com/PX4/PX4-Autopilot.git && git -C PX4-Autopilot checkout a1726d316a941af9524f6279eb293a713d8fdcac && git -C PX4-Autopilot submodule update --init --recursive" >&2
  exit 1
fi
PX4_DIR="$(cd "${PX4_DIR_INPUT}" && pwd -P)"
mkdir -p "${OUT_DIR_INPUT}"
OUT_DIR="$(cd "${OUT_DIR_INPUT}" && pwd -P)"
case "${OUT_DIR}/" in
  "${PX4_DIR}/"*) echo "ERROR: the output directory must be outside the PX4 checkout: ${OUT_DIR}" >&2; exit 1 ;;
esac

for tool in cmake ninja git; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "ERROR: ${tool} was not found (macOS: brew install cmake ninja)" >&2
    exit 1
  fi
done

BUILD_DIR="${OUT_DIR}/build/${CONFIG}"
VENV_DIR="${OUT_DIR}/venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
REQUIREMENTS="${PX4_DIR}/Tools/setup/requirements.txt"
REQUIREMENTS_STAMP="${VENV_DIR}/.px4-requirements.sha256"

# git status of the checkout and of every nested submodule, ignored files
# included. Finder's .DS_Store files are not build output and are skipped.
checkout_state() {
  {
    git -C "${PX4_DIR}" status --porcelain --ignored --ignore-submodules=none
    git -C "${PX4_DIR}" submodule foreach --recursive --quiet \
      'git status --porcelain --ignored --ignore-submodules=none | sed "s|^|${displaypath}: |"'
  } | grep -v '\.DS_Store$' || true
}

# PX4's CMake writes VS Code helper files into the source tree on every
# configure (platforms/common and platforms/posix CMakeLists.txt), with no
# option to turn it off. They are saved before the build and put back after.
GENERATED_IN_SOURCE=(.vscode/c_cpp_properties.json .vscode/launch.json)
SAVED_DIR="${OUT_DIR}/saved-source-files"

save_generated_files() {
  rm -rf "${SAVED_DIR}"
  mkdir -p "${SAVED_DIR}"
  local path
  for path in "${GENERATED_IN_SOURCE[@]}"; do
    if [[ -e "${PX4_DIR}/${path}" ]]; then
      mkdir -p "${SAVED_DIR}/$(dirname "${path}")"
      cp -p "${PX4_DIR}/${path}" "${SAVED_DIR}/${path}"
    fi
  done
}

restore_generated_files() {
  local path
  for path in "${GENERATED_IN_SOURCE[@]}"; do
    if [[ -e "${SAVED_DIR}/${path}" ]]; then
      cp -p "${SAVED_DIR}/${path}" "${PX4_DIR}/${path}"
    else
      rm -f "${PX4_DIR}/${path}"
    fi
  done
}

echo "[build-px4-sitl] px4_dir=${PX4_DIR}"
echo "[build-px4-sitl] px4_commit=$(git -C "${PX4_DIR}" rev-parse --short HEAD)"
echo "[build-px4-sitl] out_dir=${OUT_DIR}"
STATE_BEFORE="$(checkout_state)"
save_generated_files
trap restore_generated_files EXIT

requirements_hash="$(shasum -a 256 "${REQUIREMENTS}" | cut -d' ' -f1)"
if [[ ! -x "${VENV_PYTHON}" || "$(cat "${REQUIREMENTS_STAMP}" 2>/dev/null)" != "${requirements_hash}" ]]; then
  echo "[build-px4-sitl] creating the Python environment: ${VENV_DIR}"
  "${PX4_SITL_PYTHON:-python3}" -m venv "${VENV_DIR}"
  "${VENV_PYTHON}" -m pip install --quiet --upgrade pip
  "${VENV_PYTHON}" -m pip install --quiet -r "${REQUIREMENTS}"
  echo "${requirements_hash}" > "${REQUIREMENTS_STAMP}"
fi

mkdir -p "${OUT_DIR}/ccache" "${OUT_DIR}/ccache-tmp"
export CCACHE_DIR="${OUT_DIR}/ccache"
export CCACHE_TEMPDIR="${OUT_DIR}/ccache-tmp"
export VIRTUAL_ENV="${VENV_DIR}"
export PATH="${VENV_DIR}/bin:${PATH}"
unset PYTHONPATH PYTHONHOME
# PX4's code generators are Python scripts in the source tree; their bytecode
# would land in __pycache__ next to them. Keep it in the output directory.
export PYTHONPYCACHEPREFIX="${OUT_DIR}/pycache"

if [[ ! -f "${BUILD_DIR}/build.ninja" ]]; then
  echo "[build-px4-sitl] configuring: ${BUILD_DIR}"
  cmake -S "${PX4_DIR}" -B "${BUILD_DIR}" -G Ninja \
    -DCONFIG="${CONFIG}" \
    -DPYTHON_EXECUTABLE="${VENV_PYTHON}"
fi
echo "[build-px4-sitl] building"
cmake --build "${BUILD_DIR}"

restore_generated_files
STATE_AFTER="$(checkout_state)"
if [[ "${STATE_BEFORE}" != "${STATE_AFTER}" ]]; then
  echo "ERROR: the build changed the PX4 checkout:" >&2
  diff <(echo "${STATE_BEFORE}") <(echo "${STATE_AFTER}") >&2 || true
  exit 1
fi

echo "OK: ${BUILD_DIR}/bin/px4"
echo "OK: the PX4 checkout is unchanged"
