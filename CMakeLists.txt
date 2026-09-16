cmake_minimum_required(VERSION 3.21)

project(hakoniwa-urban-mobility
    VERSION 0.1.0
    DESCRIPTION "Hakoniwa urban mobility scenario applications"
    LANGUAGES C CXX
)

include(CTest)

set(_runtime_default "${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-robot-runtime")
set(_mujoco_robots_default "${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-mujoco-robots")
set(_pdu_registry_default "${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-pdu-registry")
set(_foundation_default
    "${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-business-pack/work/foundation/install")

set(HAKONIWA_ROBOT_RUNTIME_ROOT "${_runtime_default}" CACHE PATH
    "Sibling checkout of hakoniwa-robot-runtime")
set(HAKONIWA_MUJOCO_ROBOTS_ROOT "${_mujoco_robots_default}" CACHE PATH
    "Sibling checkout of hakoniwa-mujoco-robots")
set(HAKONIWA_PDU_REGISTRY_ROOT "${_pdu_registry_default}" CACHE PATH
    "Sibling checkout of hakoniwa-pdu-registry")
set(HAKONIWA_FOUNDATION_PREFIX "${_foundation_default}" CACHE PATH
    "Hakoniwa Foundation installation")
option(HAKO_URBAN_ENABLE_VIEWER "Build the native MuJoCo viewer" ON)

foreach(required_path IN ITEMS
    "${HAKONIWA_ROBOT_RUNTIME_ROOT}/src/runtime/actuator_runtime.cpp"
    "${HAKONIWA_MUJOCO_ROBOTS_ROOT}/src/CMakeLists.txt"
    "${HAKONIWA_PDU_REGISTRY_ROOT}/pdu/types/ackermann_msgs/pdu_cpptype_AckermannDrive.hpp"
    "${HAKONIWA_FOUNDATION_PREFIX}/include")
    if(NOT EXISTS "${required_path}")
        message(FATAL_ERROR "Required Urban Mobility dependency is missing: ${required_path}")
    endif()
endforeach()

find_package(hakoniwa-core CONFIG REQUIRED
    PATHS "${HAKONIWA_FOUNDATION_PREFIX}"
    NO_DEFAULT_PATH)

set(HAKO_USE_THIRDPARTY_HAKONIWA OFF CACHE BOOL "" FORCE)
set(HAKONIWA_INSTALL_PREFIX "${HAKONIWA_FOUNDATION_PREFIX}" CACHE PATH "" FORCE)
set(HAKONIWA_PDU_ENDPOINT_PREFIX "${HAKONIWA_FOUNDATION_PREFIX}" CACHE PATH "" FORCE)
set(HAKONIWA_EXTRA_PREFIX_PATH "${HAKONIWA_FOUNDATION_PREFIX}" CACHE STRING "" FORCE)
set(USE_VIEWER ${HAKO_URBAN_ENABLE_VIEWER} CACHE BOOL "" FORCE)
set(HAKO_BUILD_SENSOR_TESTS OFF CACHE BOOL "" FORCE)
set(HAKO_BUILD_CAMERA_SMOKE_TESTS OFF CACHE BOOL "" FORCE)

add_subdirectory(
    "${HAKONIWA_MUJOCO_ROBOTS_ROOT}/src"
    "${CMAKE_CURRENT_BINARY_DIR}/hakoniwa-mujoco-robots"
    EXCLUDE_FROM_ALL
)

function(hako_import_mujoco_variable output_variable source_variable)
    get_directory_property(value
        DIRECTORY "${HAKONIWA_MUJOCO_ROBOTS_ROOT}/src"
        DEFINITION "${source_variable}")
    if(NOT value)
        message(FATAL_ERROR "Could not import ${source_variable} from hakoniwa-mujoco-robots")
    endif()
    set(${output_variable} "${value}" PARENT_SCOPE)
endfunction()

hako_import_mujoco_variable(HAKO_MUJOCO_ROOT PROJECT_ROOT_DIR)
hako_import_mujoco_variable(HAKO_MUJOCO_INCLUDE MUJOCO_SOURCE_DIR)
hako_import_mujoco_variable(HAKO_MUJOCO_LIB_DIR MUJOCO_LIB_DIR)
hako_import_mujoco_variable(HAKO_MUJOCO_LIBRARY LIBMUJOCO)
hako_import_mujoco_variable(HAKO_PDU_TYPES_INCLUDE HAKO_PDU_TYPES_INCLUDE_DIR)

find_library(HAKO_PDU_ENDPOINT_LIBRARY
    NAMES hakoniwa_pdu_endpoint
    PATHS "${HAKONIWA_FOUNDATION_PREFIX}/lib"
    REQUIRED NO_DEFAULT_PATH)

set(RUNTIME_SRC "${HAKONIWA_ROBOT_RUNTIME_ROOT}/src")
set(RUNTIME_INCLUDE "${HAKONIWA_ROBOT_RUNTIME_ROOT}/include")

add_executable(urban-car-hakoniwa-asset
    apps/car/urban-car-hakoniwa-asset.cpp
    ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory.cpp
    ${RUNTIME_SRC}/runtime/source/scalar_pdu_command_source.cpp
    ${RUNTIME_SRC}/runtime/source/joint_trajectory_command_source.cpp
    ${RUNTIME_SRC}/runtime/source/joy_command_source.cpp
    ${RUNTIME_SRC}/runtime/source/ackermann_drive_command_source.cpp
    ${RUNTIME_SRC}/runtime/controller/scalar_pdu_command_controller.cpp
    ${RUNTIME_SRC}/runtime/controller/joint_trajectory_controller.cpp
    ${RUNTIME_SRC}/runtime/controller/manual_controller.cpp
    ${RUNTIME_SRC}/runtime/controller/hold_controller.cpp
    ${RUNTIME_SRC}/runtime/controller/ackermann_controller.cpp
    ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
    ${RUNTIME_SRC}/runtime/publisher/joint_state_publisher.cpp
    ${RUNTIME_SRC}/runtime/publisher/multi_dof_joint_state_publisher.cpp
    ${RUNTIME_SRC}/adapters/physics/mujoco/mujoco_actuator_plant.cpp
    ${RUNTIME_SRC}/adapters/endpoint/float64_pdu_event_reader.cpp
    ${RUNTIME_SRC}/adapters/endpoint/joint_state_pdu_writer.cpp
    ${RUNTIME_SRC}/adapters/endpoint/joint_trajectory_pdu_event_reader.cpp
    ${RUNTIME_SRC}/adapters/endpoint/joy_pdu_event_reader.cpp
    ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_pdu_event_reader.cpp
    ${RUNTIME_SRC}/adapters/endpoint/multi_dof_joint_state_pdu_writer.cpp
    ${RUNTIME_SRC}/runner/runner_factory.cpp
    ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp
    ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_runner.cpp
    ${RUNTIME_SRC}/factory/manifest_factory.cpp
    ${RUNTIME_SRC}/factory/manifest_factory_manifest.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_manifest.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_actuator.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_trajectory.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_manual.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_ackermann.cpp
    ${RUNTIME_SRC}/runtime/runtime_factory_state_output.cpp
    ${HAKO_MUJOCO_ROOT}/src/config/asset_manifest.cpp
)

set_target_properties(urban-car-hakoniwa-asset PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
target_compile_features(urban-car-hakoniwa-asset PRIVATE cxx_std_20)
target_compile_definitions(urban-car-hakoniwa-asset PRIVATE
    HAKONIWA_ROBOT_RUNTIME_ENABLE_MOBILE_BASE=1
    HAKO_URBAN_ENABLE_VIEWER=$<BOOL:${HAKO_URBAN_ENABLE_VIEWER}>)
target_include_directories(urban-car-hakoniwa-asset PRIVATE
    ${RUNTIME_INCLUDE}
    ${RUNTIME_SRC}
    ${HAKO_MUJOCO_ROOT}/src
    ${HAKO_MUJOCO_ROOT}/include
    ${HAKONIWA_FOUNDATION_PREFIX}/include
    ${HAKONIWA_FOUNDATION_PREFIX}/include/hakoniwa
    ${HAKONIWA_PDU_REGISTRY_ROOT}/pdu/types
    ${HAKO_PDU_TYPES_INCLUDE})
target_include_directories(urban-car-hakoniwa-asset SYSTEM PRIVATE
    ${HAKO_MUJOCO_INCLUDE}
    ${HAKO_MUJOCO_ROOT}/thirdparty/nolman/single_include)
if(HAKO_MUJOCO_LIB_DIR)
    target_link_directories(urban-car-hakoniwa-asset PRIVATE ${HAKO_MUJOCO_LIB_DIR})
endif()
target_link_libraries(urban-car-hakoniwa-asset PRIVATE
    mujoco-common
    ${HAKO_MUJOCO_LIBRARY}
    hakoniwa-core::assets
    hakoniwa-core::conductor
    ${HAKO_PDU_ENDPOINT_LIBRARY})

if(MSVC)
    target_compile_options(urban-car-hakoniwa-asset PRIVATE
        /W4 /external:W0 /wd4710 /wd4711 /wd5220 /wd5247)
endif()

if(BUILD_TESTING)
    add_executable(hako-mobile-base-runtime-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_mobile_base_runtime.cpp
        ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
        ${RUNTIME_SRC}/runtime/source/scalar_pdu_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/joint_trajectory_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/joy_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/ackermann_drive_command_source.cpp
        ${RUNTIME_SRC}/runtime/controller/scalar_pdu_command_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/joint_trajectory_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/manual_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/hold_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/ackermann_controller.cpp
        ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
        ${RUNTIME_SRC}/runtime/publisher/joint_state_publisher.cpp
        ${RUNTIME_SRC}/runtime/publisher/multi_dof_joint_state_publisher.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory.cpp)
    target_compile_features(hako-mobile-base-runtime-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-mobile-base-runtime-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_MOBILE_BASE=1)
    target_include_directories(hako-mobile-base-runtime-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC})
    add_test(NAME hako-mobile-base-runtime-contract
        COMMAND hako-mobile-base-runtime-test)
endif()
