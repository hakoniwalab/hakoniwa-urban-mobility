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
# Builds the opt-in Robot Runtime Plant Directive path (Mirror + geom friction)
# by defining HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE.
option(HAKO_URBAN_ENABLE_MIRROR
    "Build the opt-in Robot Runtime Plant Directive extension (Mirror, geom friction)" OFF)
option(HAKO_ENABLE_ASYNC_RPC_INPUT
    "Build the opt-in Robot Runtime asynchronous RPC input extension" OFF)
# Robot Runtime async services, two rates and observation/estimate
# (hakoniwa-robot-runtime docs/design-async-rpc.md, section 3.3). One switch
# for the three; it needs HAKO_ENABLE_ASYNC_RPC_INPUT for the Core channel.
option(HAKO_ENABLE_ASYNC_SERVICE
    "Build the opt-in Robot Runtime async service extension" OFF)
if(HAKO_ENABLE_ASYNC_SERVICE AND NOT HAKO_ENABLE_ASYNC_RPC_INPUT)
    message(FATAL_ERROR "HAKO_ENABLE_ASYNC_SERVICE needs HAKO_ENABLE_ASYNC_RPC_INPUT")
endif()
add_compile_definitions(
    HAKONIWA_ROBOT_RUNTIME_ENABLE_ASYNC_SERVICE=$<BOOL:${HAKO_ENABLE_ASYNC_SERVICE}>)

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
set(LOCKSTEP_GATE_SOURCE ${RUNTIME_SRC}/runner/lockstep_gate.cpp)
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
    ${LOCKSTEP_GATE_SOURCE}
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

if(HAKO_ENABLE_ASYNC_RPC_INPUT)
    target_sources(urban-car-hakoniwa-asset PRIVATE
        ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
        ${RUNTIME_SRC}/adapters/endpoint/person_step_rpc_event_reader.cpp
        ${RUNTIME_SRC}/runner/async_rpc_input.cpp
        ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp)
endif()

if(HAKO_ENABLE_ASYNC_SERVICE)
    set(ASYNC_SERVICE_SOURCES
        ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp
        ${RUNTIME_SRC}/runner/hakoniwa/core_async_service_server.cpp)
    set(ACKERMANN_STEP_SERVICE_SOURCE
        ${RUNTIME_SRC}/adapters/endpoint/ackermann_step_service.cpp)
    set(PERSON_STEP_SERVICE_SOURCE
        ${RUNTIME_SRC}/adapters/endpoint/person_step_service.cpp)
    target_sources(urban-car-hakoniwa-asset PRIVATE
        ${ASYNC_SERVICE_SOURCES}
        ${ACKERMANN_STEP_SERVICE_SOURCE}
        ${PERSON_STEP_SERVICE_SOURCE})
endif()

if(HAKO_URBAN_ENABLE_MIRROR)
    target_sources(urban-car-hakoniwa-asset PRIVATE
        ${RUNTIME_SRC}/runtime/source/mirror_body_state_source.cpp
        ${RUNTIME_SRC}/runtime/controller/mirror_body_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/geom_friction_controller.cpp
        ${RUNTIME_SRC}/runtime/publisher/impulse_collision_publisher.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_mirror.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_geom_friction.cpp
        ${RUNTIME_SRC}/adapters/endpoint/mirror_body_twist_pdu_reader.cpp
        ${RUNTIME_SRC}/adapters/endpoint/impulse_collision_pdu_writer.cpp)
endif()

set_target_properties(urban-car-hakoniwa-asset PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
if(CMAKE_CONFIGURATION_TYPES)
    foreach(config IN LISTS CMAKE_CONFIGURATION_TYPES)
        string(TOUPPER "${config}" config_upper)
        set_target_properties(urban-car-hakoniwa-asset PROPERTIES
            "RUNTIME_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/bin")
    endforeach()
endif()
# What this plant is built with, next to it, including for packaged plants.
if(HAKO_URBAN_ENABLE_MIRROR)
    set(_urban_plant_directive true)
else()
    set(_urban_plant_directive false)
endif()
if(HAKO_ENABLE_ASYNC_SERVICE)
    set(_urban_async_service true)
else()
    set(_urban_async_service false)
endif()
file(CONFIGURE OUTPUT "${CMAKE_BINARY_DIR}/bin/urban-car-hakoniwa-asset.features.json"
    CONTENT "{\n  \"schema_version\": 1,\n  \"plant_directive\": @_urban_plant_directive@,\n  \"async_service\": @_urban_async_service@\n}\n"
    @ONLY)
target_compile_features(urban-car-hakoniwa-asset PRIVATE cxx_std_20)
target_compile_definitions(urban-car-hakoniwa-asset PRIVATE
    HAKONIWA_ROBOT_RUNTIME_ENABLE_MOBILE_BASE=1
    # --lockstep-input (repeatable P0 runs; off unless the option is given)
    HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1
    HAKO_ENABLE_ASYNC_RPC_INPUT=$<BOOL:${HAKO_ENABLE_ASYNC_RPC_INPUT}>
    HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=$<BOOL:${HAKO_URBAN_ENABLE_MIRROR}>
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

if(WIN32)
    # The Launcher starts this executable from outside the build tree. Stage
    # its runtime DLL closure next to the executable so Windows can resolve
    # MuJoCo and any other shared runtime dependencies before main() runs.
    file(GLOB _urban_foundation_runtime_dlls
        "${HAKONIWA_FOUNDATION_PREFIX}/bin/*.dll")
    add_custom_command(TARGET urban-car-hakoniwa-asset POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_RUNTIME_DLLS:urban-car-hakoniwa-asset>
            $<TARGET_FILE_DIR:urban-car-hakoniwa-asset>
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:MuJoCo::mujoco>"
            $<TARGET_FILE_DIR:urban-car-hakoniwa-asset>
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            ${_urban_foundation_runtime_dlls}
            $<TARGET_FILE_DIR:urban-car-hakoniwa-asset>
        COMMAND_EXPAND_LISTS)
endif()

if(MSVC)
    target_compile_options(urban-car-hakoniwa-asset PRIVATE
        /W4 /external:W0 /wd4710 /wd4711 /wd5220 /wd5247)
endif()

if(BUILD_TESTING)
    add_executable(hako-mirror-mujoco-plant-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_mirror_mujoco_plant.cpp
        ${RUNTIME_SRC}/adapters/physics/mujoco/mujoco_actuator_plant.cpp)
    target_compile_features(hako-mirror-mujoco-plant-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-mirror-mujoco-plant-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-mirror-mujoco-plant-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC}
        ${HAKO_MUJOCO_ROOT}/src
        ${HAKO_MUJOCO_ROOT}/include)
    target_include_directories(hako-mirror-mujoco-plant-test SYSTEM PRIVATE
        ${HAKO_MUJOCO_INCLUDE})
    if(HAKO_MUJOCO_LIB_DIR)
        target_link_directories(hako-mirror-mujoco-plant-test PRIVATE
            ${HAKO_MUJOCO_LIB_DIR})
    endif()
    target_link_libraries(hako-mirror-mujoco-plant-test PRIVATE
        mujoco-common
        ${HAKO_MUJOCO_LIBRARY})

    add_executable(hako-mirror-configuration-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_mirror_configuration.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_manifest.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_actuator.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_trajectory.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_manual.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_state_output.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_mirror.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_geom_friction.cpp)
    target_compile_features(hako-mirror-configuration-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-mirror-configuration-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-mirror-configuration-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC}
        ${HAKO_MUJOCO_ROOT}/thirdparty/nolman/single_include)

    add_executable(hako-mirror-runtime-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_mirror_runtime.cpp
        ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
        ${RUNTIME_SRC}/runtime/source/mirror_body_state_source.cpp
        ${RUNTIME_SRC}/runtime/controller/mirror_body_controller.cpp
        ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
        ${RUNTIME_SRC}/runtime/publisher/impulse_collision_publisher.cpp)
    target_compile_features(hako-mirror-runtime-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-mirror-runtime-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-mirror-runtime-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC})

    add_executable(hako-geom-friction-mujoco-plant-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_geom_friction_mujoco_plant.cpp
        ${RUNTIME_SRC}/adapters/physics/mujoco/mujoco_actuator_plant.cpp)
    target_compile_features(hako-geom-friction-mujoco-plant-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-geom-friction-mujoco-plant-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-geom-friction-mujoco-plant-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC}
        ${HAKO_MUJOCO_ROOT}/src
        ${HAKO_MUJOCO_ROOT}/include)
    target_include_directories(hako-geom-friction-mujoco-plant-test SYSTEM PRIVATE
        ${HAKO_MUJOCO_INCLUDE})
    if(HAKO_MUJOCO_LIB_DIR)
        target_link_directories(hako-geom-friction-mujoco-plant-test PRIVATE
            ${HAKO_MUJOCO_LIB_DIR})
    endif()
    target_link_libraries(hako-geom-friction-mujoco-plant-test PRIVATE
        mujoco-common
        ${HAKO_MUJOCO_LIBRARY})

    add_executable(hako-geom-friction-configuration-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_geom_friction_configuration.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_manifest.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_actuator.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_trajectory.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_manual.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_state_output.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_mirror.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory_geom_friction.cpp)
    target_compile_features(hako-geom-friction-configuration-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-geom-friction-configuration-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-geom-friction-configuration-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC}
        ${HAKO_MUJOCO_ROOT}/thirdparty/nolman/single_include)

    add_executable(hako-geom-friction-runtime-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_geom_friction_runtime.cpp
        ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
        ${RUNTIME_SRC}/runtime/runtime_factory.cpp
        ${RUNTIME_SRC}/runtime/source/scalar_pdu_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/joint_trajectory_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/joy_command_source.cpp
        ${RUNTIME_SRC}/runtime/source/mirror_body_state_source.cpp
        ${RUNTIME_SRC}/runtime/controller/scalar_pdu_command_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/joint_trajectory_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/manual_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/hold_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/mirror_body_controller.cpp
        ${RUNTIME_SRC}/runtime/controller/geom_friction_controller.cpp
        ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
        ${RUNTIME_SRC}/runtime/publisher/joint_state_publisher.cpp
        ${RUNTIME_SRC}/runtime/publisher/impulse_collision_publisher.cpp)
    target_compile_features(hako-geom-friction-runtime-test PRIVATE cxx_std_20)
    target_compile_definitions(hako-geom-friction-runtime-test PRIVATE
        HAKONIWA_ROBOT_RUNTIME_ENABLE_PLANT_DIRECTIVE=1)
    target_include_directories(hako-geom-friction-runtime-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC})

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
    add_executable(hako-lockstep-driver-test
        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_hakoniwa_asset_driver_lockstep.cpp
        ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
    target_compile_features(hako-lockstep-driver-test PRIVATE cxx_std_20)
    target_include_directories(hako-lockstep-driver-test PRIVATE
        ${RUNTIME_INCLUDE}
        ${RUNTIME_SRC})
    target_link_libraries(hako-lockstep-driver-test PRIVATE hakoniwa-core::assets)
    add_test(NAME hako-lockstep-driver-contract
        COMMAND hako-lockstep-driver-test)
    if(HAKO_ENABLE_ASYNC_RPC_INPUT)
        add_executable(hako-async-rpc-input-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
        target_compile_features(hako-async-rpc-input-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-async-rpc-input-test PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-async-rpc-input-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC})
        target_link_libraries(hako-async-rpc-input-test PRIVATE hakoniwa-core::assets)
        add_test(NAME hako-async-rpc-input-contract
            COMMAND hako-async-rpc-input-test)

        add_executable(hako-ackermann-rpc-input-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_ackermann_rpc_input.cpp
            ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp)
        target_compile_features(hako-ackermann-rpc-input-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-ackermann-rpc-input-test PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-ackermann-rpc-input-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-ackermann-rpc-input-test PRIVATE
            hakoniwa-core::assets)
        add_test(NAME hako-ackermann-rpc-input-contract
            COMMAND hako-ackermann-rpc-input-test)

        add_executable(hako-person-rpc-input-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_person_rpc_input.cpp
            ${RUNTIME_SRC}/adapters/endpoint/person_step_rpc_event_reader.cpp
            ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
            ${RUNTIME_SRC}/adapters/physics/mujoco/mujoco_actuator_plant.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp)
        target_compile_features(hako-person-rpc-input-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-person-rpc-input-test PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-person-rpc-input-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_MUJOCO_ROOT}/src
            ${HAKO_MUJOCO_ROOT}/include
            ${HAKO_PDU_TYPES_INCLUDE})
        target_include_directories(hako-person-rpc-input-test SYSTEM PRIVATE
            ${HAKO_MUJOCO_INCLUDE})
        if(HAKO_MUJOCO_LIB_DIR)
            target_link_directories(hako-person-rpc-input-test PRIVATE
                ${HAKO_MUJOCO_LIB_DIR})
        endif()
        target_link_libraries(hako-person-rpc-input-test PRIVATE
            mujoco-common
            ${HAKO_MUJOCO_LIBRARY}
            hakoniwa-core::assets)
        add_test(NAME hako-person-rpc-input-contract
            COMMAND hako-person-rpc-input-test)
        if(HAKO_ENABLE_ASYNC_SERVICE)
            target_sources(hako-person-rpc-input-test PRIVATE ${PERSON_STEP_SERVICE_SOURCE})
            add_test(NAME hako-person-step-service-contract
                COMMAND hako-person-rpc-input-test --service)
        endif()
        add_test(NAME hako-rpc-service-config-generator-contract
            COMMAND
                "${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3"
                "${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_gen_rpc_service_config.py")

        add_executable(hako-async-rpc-integration-server
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_rpc_server.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
        target_compile_features(hako-async-rpc-integration-server PRIVATE cxx_std_20)
        target_compile_definitions(hako-async-rpc-integration-server PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-async-rpc-integration-server PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-async-rpc-integration-server PRIVATE
            hakoniwa-core::assets)

        add_executable(hako-async-rpc-integration-client
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_rpc_client.cpp)
        target_compile_features(hako-async-rpc-integration-client PRIVATE cxx_std_20)
        target_include_directories(hako-async-rpc-integration-client PRIVATE
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-async-rpc-integration-client PRIVATE
            hakoniwa-core::shakoc)

        add_executable(hako-ackermann-rpc-integration-server
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/ackermann_rpc_server.cpp
            ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
        target_compile_features(hako-ackermann-rpc-integration-server PRIVATE cxx_std_20)
        target_compile_definitions(hako-ackermann-rpc-integration-server PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-ackermann-rpc-integration-server PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-ackermann-rpc-integration-server PRIVATE
            hakoniwa-core::assets)

        add_executable(hako-ackermann-rpc-integration-client
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/ackermann_rpc_client.cpp)
        target_compile_features(hako-ackermann-rpc-integration-client PRIVATE cxx_std_20)
        target_include_directories(hako-ackermann-rpc-integration-client PRIVATE
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-ackermann-rpc-integration-client PRIVATE
            hakoniwa-core::shakoc)

        add_executable(hako-person-rpc-integration-server
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/person_rpc_server.cpp
            ${RUNTIME_SRC}/adapters/endpoint/person_step_rpc_event_reader.cpp
            ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
        target_compile_features(hako-person-rpc-integration-server PRIVATE cxx_std_20)
        target_compile_definitions(hako-person-rpc-integration-server PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-person-rpc-integration-server PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-person-rpc-integration-server PRIVATE
            hakoniwa-core::assets)

        add_executable(hako-person-rpc-integration-client
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/person_rpc_client.cpp)
        target_compile_features(hako-person-rpc-integration-client PRIVATE cxx_std_20)
        target_include_directories(hako-person-rpc-integration-client PRIVATE
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-person-rpc-integration-client PRIVATE
            hakoniwa-core::shakoc)

        if(HAKO_ENABLE_ASYNC_SERVICE)
        target_compile_definitions(hako-person-rpc-integration-server PRIVATE
            HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
        target_sources(hako-person-rpc-integration-server PRIVATE
            ${PERSON_STEP_SERVICE_SOURCE}
            ${ASYNC_SERVICE_SOURCES}
            ${LOCKSTEP_GATE_SOURCE}
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp)
        # Stage A of hakoniwa-robot-runtime docs/design-async-rpc.md: cancel as an event.
        add_executable(hako-async-rpc-cancel-server
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_rpc_cancel_server.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
        target_compile_features(hako-async-rpc-cancel-server PRIVATE cxx_std_20)
        target_compile_definitions(hako-async-rpc-cancel-server PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1)
        target_include_directories(hako-async-rpc-cancel-server PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-async-rpc-cancel-server PRIVATE
            hakoniwa-core::assets)

        add_executable(hako-async-rpc-cancel-client
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_rpc_cancel_client.cpp)
        target_compile_features(hako-async-rpc-cancel-client PRIVATE cxx_std_20)
        target_include_directories(hako-async-rpc-cancel-client PRIVATE
            ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-async-rpc-cancel-client PRIVATE
            hakoniwa-core::shakoc)
        endif()

        add_executable(hako-async-rpc-integration-conductor
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_rpc_conductor.cpp)
        target_compile_features(hako-async-rpc-integration-conductor PRIVATE cxx_std_20)
        target_include_directories(hako-async-rpc-integration-conductor PRIVATE
            ${HAKONIWA_FOUNDATION_PREFIX}/include/hakoniwa)
        target_link_libraries(hako-async-rpc-integration-conductor PRIVATE
            hakoniwa-core::conductor)

        if(UNIX)
            add_test(NAME hako-async-rpc-input-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_async_rpc_input.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-async-rpc-input-integration PROPERTIES
                TIMEOUT 60
                LABELS integration)
            add_test(NAME hako-ackermann-rpc-input-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_rpc_input.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-ackermann-rpc-input-integration PROPERTIES
                TIMEOUT 60
                LABELS integration)
            add_test(NAME hako-person-rpc-input-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_person_rpc_input.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-person-rpc-input-integration PROPERTIES
                TIMEOUT 60
                LABELS integration)
            if(HAKO_ENABLE_ASYNC_SERVICE)
            add_test(NAME hako-person-step-service-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_person_rpc_input.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration compare)
            set_tests_properties(hako-person-step-service-integration PROPERTIES
                TIMEOUT 60 LABELS integration)
            add_test(NAME hako-async-rpc-cancel-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_async_rpc_cancel.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-async-rpc-cancel-integration PROPERTIES
                TIMEOUT 60
                LABELS integration)

            add_executable(hako-async-service-reply-server
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_service_reply_server.cpp
                ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp
                ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
                ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
                ${ASYNC_SERVICE_SOURCES})
            target_compile_features(hako-async-service-reply-server PRIVATE cxx_std_20)
            target_compile_definitions(hako-async-service-reply-server PRIVATE
                HAKO_ENABLE_ASYNC_RPC_INPUT=1)
            target_include_directories(hako-async-service-reply-server PRIVATE
                ${RUNTIME_INCLUDE}
                ${RUNTIME_SRC}
                ${HAKO_PDU_TYPES_INCLUDE})
            target_link_libraries(hako-async-service-reply-server PRIVATE hakoniwa-core::assets)

            add_executable(hako-async-service-reply-client
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_service_reply_client.cpp)
            target_compile_features(hako-async-service-reply-client PRIVATE cxx_std_20)
            target_include_directories(hako-async-service-reply-client PRIVATE
                ${HAKO_PDU_TYPES_INCLUDE})
            target_link_libraries(hako-async-service-reply-client PRIVATE hakoniwa-core::shakoc)

            add_test(NAME hako-async-service-reply-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_async_service_reply.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-async-service-reply-integration PROPERTIES
                TIMEOUT 60
                LABELS integration)
            foreach(scenario IN ITEMS long busy cancel)
                add_test(NAME hako-async-service-${scenario}-integration
                    COMMAND bash
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_async_service_reply.bash
                        ${CMAKE_CURRENT_BINARY_DIR}
                        ${HAKONIWA_FOUNDATION_PREFIX}
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                        ${scenario})
                set_tests_properties(hako-async-service-${scenario}-integration PROPERTIES
                    TIMEOUT 60
                    LABELS integration)
            endforeach()

            add_executable(hako-async-service-lockstep-server
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_service_lockstep_server.cpp
                ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp
                ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
                ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
                ${ASYNC_SERVICE_SOURCES}
                ${LOCKSTEP_GATE_SOURCE})
            target_compile_features(hako-async-service-lockstep-server PRIVATE cxx_std_20)
            target_compile_definitions(hako-async-service-lockstep-server PRIVATE
                HAKO_ENABLE_ASYNC_RPC_INPUT=1
                HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
            target_include_directories(hako-async-service-lockstep-server PRIVATE
                ${RUNTIME_INCLUDE} ${RUNTIME_SRC} ${HAKO_PDU_TYPES_INCLUDE})
            target_link_libraries(hako-async-service-lockstep-server PRIVATE hakoniwa-core::assets)

            add_executable(hako-async-service-lockstep-client
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/async_service_lockstep_client.cpp)
            target_compile_features(hako-async-service-lockstep-client PRIVATE cxx_std_20)
            target_link_libraries(hako-async-service-lockstep-client PRIVATE hakoniwa-core::shakoc)

            foreach(scenario IN ITEMS deterministic timeout)
                add_test(NAME hako-async-service-lockstep-${scenario}-integration
                    COMMAND bash
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_async_service_lockstep.bash
                        ${CMAKE_CURRENT_BINARY_DIR}
                        ${HAKONIWA_FOUNDATION_PREFIX}
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                        ${scenario})
                set_tests_properties(hako-async-service-lockstep-${scenario}-integration PROPERTIES
                    TIMEOUT 60
                    LABELS integration)
            endforeach()

            add_executable(hako-ackermann-step-service-server
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/ackermann_step_service_server.cpp
                ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
                ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
                ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp
                ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
                ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
                ${ASYNC_SERVICE_SOURCES}
                ${LOCKSTEP_GATE_SOURCE}
                ${ACKERMANN_STEP_SERVICE_SOURCE})
            target_compile_features(hako-ackermann-step-service-server PRIVATE cxx_std_20)
            target_compile_definitions(hako-ackermann-step-service-server PRIVATE
                HAKO_ENABLE_ASYNC_RPC_INPUT=1
                HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
            target_include_directories(hako-ackermann-step-service-server PRIVATE
                ${RUNTIME_INCLUDE} ${RUNTIME_SRC} ${HAKO_PDU_TYPES_INCLUDE})
            target_link_libraries(hako-ackermann-step-service-server PRIVATE hakoniwa-core::assets)
            add_test(NAME hako-ackermann-step-service-integration
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_step_service.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration)
            set_tests_properties(hako-ackermann-step-service-integration PROPERTIES
                TIMEOUT 60 LABELS integration)
            foreach(scenario IN ITEMS two timeout)
                add_test(NAME hako-ackermann-step-service-${scenario}-integration
                    COMMAND bash
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_step_service.bash
                        ${CMAKE_CURRENT_BINARY_DIR}
                        ${HAKONIWA_FOUNDATION_PREFIX}
                        ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                        "" ${scenario})
                set_tests_properties(hako-ackermann-step-service-${scenario}-integration PROPERTIES
                    TIMEOUT 60 LABELS integration)
            endforeach()
            add_executable(hako-ackermann-step-urban-client
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/ackermann_step_urban_client.cpp)
            target_compile_features(hako-ackermann-step-urban-client PRIVATE cxx_std_20)
            target_link_libraries(hako-ackermann-step-urban-client PRIVATE hakoniwa-core::shakoc)
            add_test(NAME urban-ackermann-step-service-repeatability
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_step_service.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                    ${CMAKE_CURRENT_SOURCE_DIR})
            set_tests_properties(urban-ackermann-step-service-repeatability PROPERTIES
                TIMEOUT 60 LABELS integration)
            add_test(NAME urban-ackermann-step-service-two-repeatability
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_step_service.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                    ${CMAKE_CURRENT_SOURCE_DIR} two)
            set_tests_properties(urban-ackermann-step-service-two-repeatability PROPERTIES
                TIMEOUT 60 LABELS integration)
            add_executable(hako-ackermann-step-startup-client
                ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/ackermann_step_startup_client.cpp
                ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp)
            target_compile_features(hako-ackermann-step-startup-client PRIVATE cxx_std_20)
            target_compile_definitions(hako-ackermann-step-startup-client PRIVATE HAKO_ENABLE_ASYNC_RPC_INPUT=1)
            target_include_directories(hako-ackermann-step-startup-client PRIVATE ${RUNTIME_SRC})
            target_link_libraries(hako-ackermann-step-startup-client PRIVATE hakoniwa-core::assets)
            add_test(NAME urban-ackermann-step-service-startup
                COMMAND bash
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/test_ackermann_step_service.bash
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration
                    ${CMAKE_CURRENT_SOURCE_DIR} startup)
            set_tests_properties(urban-ackermann-step-service-startup PROPERTIES
                TIMEOUT 60 LABELS integration)

            # Linking accepts either form, but the Python model compiler needs a file.
            if(TARGET "${HAKO_MUJOCO_LIBRARY}")
                set(HAKO_MUJOCO_LIBRARY_FILE "$<TARGET_FILE:${HAKO_MUJOCO_LIBRARY}>")
            elseif(IS_ABSOLUTE "${HAKO_MUJOCO_LIBRARY}")
                set(HAKO_MUJOCO_LIBRARY_FILE "${HAKO_MUJOCO_LIBRARY}")
            else()
                find_library(HAKO_MUJOCO_LIBRARY_FILE
                    NAMES "${HAKO_MUJOCO_LIBRARY}"
                    PATHS "${HAKO_MUJOCO_LIB_DIR}"
                    NO_DEFAULT_PATH NO_CACHE REQUIRED)
            endif()
            add_test(NAME urban-person-step-service-mixed-repeatability
                COMMAND ${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/person_step_urban.py
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${CMAKE_CURRENT_SOURCE_DIR}
                    "${HAKO_MUJOCO_LIBRARY_FILE}")
            set_tests_properties(urban-person-step-service-mixed-repeatability PROPERTIES
                TIMEOUT 120 LABELS integration)
            add_test(NAME urban-service-manifest-contract
                COMMAND ${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3
                    ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/integration/person_step_urban.py
                    ${CMAKE_CURRENT_BINARY_DIR}
                    ${HAKONIWA_FOUNDATION_PREFIX}
                    ${CMAKE_CURRENT_SOURCE_DIR}
                    "${HAKO_MUJOCO_LIBRARY_FILE}" --contract)
            set_tests_properties(urban-service-manifest-contract PROPERTIES
                TIMEOUT 90 LABELS integration)
            endif()
        endif()
    endif()
    if(HAKO_ENABLE_ASYNC_SERVICE)
        # Stage B0 of robot-runtime docs/design-async-rpc.md: two rates.
        add_executable(hako-runtime-two-rates-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_runtime_two_rates.cpp
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp)
        target_compile_features(hako-runtime-two-rates-test PRIVATE cxx_std_20)
        target_include_directories(hako-runtime-two-rates-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC})
        add_test(NAME hako-runtime-two-rates-contract
            COMMAND hako-runtime-two-rates-test)

        add_executable(hako-async-service-zero-step-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_async_service_zero_step.cpp
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
            ${RUNTIME_SRC}/runtime/controller/hold_controller.cpp
            ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp)
        target_compile_features(hako-async-service-zero-step-test PRIVATE cxx_std_20)
        target_include_directories(hako-async-service-zero-step-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC})
        add_test(NAME hako-async-service-zero-step-contract
            COMMAND hako-async-service-zero-step-test)

        add_executable(hako-async-service-lockstep-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_async_service_lockstep.cpp
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
            ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp
            ${LOCKSTEP_GATE_SOURCE})
        target_compile_features(hako-async-service-lockstep-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-async-service-lockstep-test PRIVATE
            HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
        target_include_directories(hako-async-service-lockstep-test PRIVATE
            ${RUNTIME_INCLUDE} ${RUNTIME_SRC})
        add_test(NAME hako-async-service-lockstep-contract
            COMMAND hako-async-service-lockstep-test)

        add_executable(hako-async-service-lockstep-disabled-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_async_service_lockstep.cpp
            ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp)
        target_compile_features(hako-async-service-lockstep-disabled-test PRIVATE cxx_std_20)
        target_include_directories(hako-async-service-lockstep-disabled-test PRIVATE
            ${RUNTIME_INCLUDE} ${RUNTIME_SRC})
        add_test(NAME hako-async-service-lockstep-disabled-contract
            COMMAND hako-async-service-lockstep-disabled-test)

        add_executable(hako-async-service-lockstep-runner-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_hakoniwa_runner_async_service_lockstep.cpp
            ${RUNTIME_SRC}/adapters/physics/mujoco/mujoco_actuator_plant.cpp
            ${RUNTIME_SRC}/runner/async_rpc_input.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_asset_driver.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/hakoniwa_runner.cpp
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
            ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp
            ${LOCKSTEP_GATE_SOURCE})
        target_compile_features(hako-async-service-lockstep-runner-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-async-service-lockstep-runner-test PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1
            HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
        target_include_directories(hako-async-service-lockstep-runner-test PRIVATE
            ${RUNTIME_INCLUDE}
            ${RUNTIME_SRC}
            ${HAKO_MUJOCO_ROOT}/src
            ${HAKO_MUJOCO_ROOT}/include
            ${HAKONIWA_FOUNDATION_PREFIX}/include
            ${HAKONIWA_FOUNDATION_PREFIX}/include/hakoniwa)
        target_include_directories(hako-async-service-lockstep-runner-test SYSTEM PRIVATE
            ${HAKO_MUJOCO_INCLUDE}
            ${HAKO_MUJOCO_ROOT}/thirdparty/nolman/single_include)
        if(HAKO_MUJOCO_LIB_DIR)
            target_link_directories(hako-async-service-lockstep-runner-test PRIVATE
                ${HAKO_MUJOCO_LIB_DIR})
        endif()
        target_link_libraries(hako-async-service-lockstep-runner-test PRIVATE
            mujoco-common
            ${HAKO_MUJOCO_LIBRARY}
            hakoniwa-core::assets
            hakoniwa-core::conductor
            ${HAKO_PDU_ENDPOINT_LIBRARY})
        add_test(NAME hako-async-service-lockstep-runner-contract
            COMMAND hako-async-service-lockstep-runner-test)
        set_tests_properties(hako-async-service-lockstep-runner-contract PROPERTIES TIMEOUT 10)

        add_executable(hako-ackermann-step-service-test
            ${HAKONIWA_ROBOT_RUNTIME_ROOT}/tests/test_ackermann_step_service.cpp
            ${RUNTIME_SRC}/adapters/endpoint/ackermann_drive_rpc_event_reader.cpp
            ${RUNTIME_SRC}/runner/hakoniwa/async_rpc_service_channel.cpp
            ${RUNTIME_SRC}/runtime/actuator_runtime.cpp
            ${RUNTIME_SRC}/runtime/arbiter/priority_command_arbiter.cpp
            ${RUNTIME_SRC}/runtime/controller/ackermann_controller.cpp
            ${RUNTIME_SRC}/runtime/source/ackermann_drive_command_source.cpp
            ${RUNTIME_SRC}/runtime/service/async_service_pipeline.cpp
            ${LOCKSTEP_GATE_SOURCE}
            ${ACKERMANN_STEP_SERVICE_SOURCE})
        target_compile_features(hako-ackermann-step-service-test PRIVATE cxx_std_20)
        target_compile_definitions(hako-ackermann-step-service-test PRIVATE
            HAKO_ENABLE_ASYNC_RPC_INPUT=1
            HAKONIWA_ROBOT_RUNTIME_ENABLE_LOCKSTEP=1)
        target_include_directories(hako-ackermann-step-service-test PRIVATE
            ${RUNTIME_INCLUDE} ${RUNTIME_SRC}
            ${HAKONIWA_PDU_REGISTRY_ROOT}/pdu/types ${HAKO_PDU_TYPES_INCLUDE})
        target_link_libraries(hako-ackermann-step-service-test PRIVATE hakoniwa-core::assets)
        add_test(NAME hako-ackermann-step-service-contract
            COMMAND hako-ackermann-step-service-test)
    endif()
    add_test(NAME hako-mirror-runtime-contract
        COMMAND hako-mirror-runtime-test)
    add_test(NAME hako-mirror-mujoco-plant-contract
        COMMAND hako-mirror-mujoco-plant-test)
    add_test(NAME hako-mirror-configuration-contract
        COMMAND hako-mirror-configuration-test)
    add_test(NAME hako-geom-friction-runtime-contract
        COMMAND hako-geom-friction-runtime-test)
    add_test(NAME hako-geom-friction-mujoco-plant-contract
        COMMAND hako-geom-friction-mujoco-plant-test)
    add_test(NAME hako-geom-friction-configuration-contract
        COMMAND hako-geom-friction-configuration-test)
    add_test(NAME urban-car-control-mode-contract
        COMMAND
            "${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_control_modes.py")
    set_tests_properties(urban-car-control-mode-contract PROPERTIES
        ENVIRONMENT
            "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/tools;HAKONIWA_WORKSPACE_ACTIVE=1;HAKONIWA_WORKSPACE_ROOT=${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-business-pack;HAKONIWA_WORK_DIR=${CMAKE_CURRENT_SOURCE_DIR}/../hakoniwa-business-pack/work")
    add_test(NAME urban-car-scenario-contract
        COMMAND
            "${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_scenario_executor.py")
    add_test(NAME urban-golf-cart-demo-scenario-contract
        COMMAND
            "${HAKONIWA_FOUNDATION_PREFIX}/python/bin/python3"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_car_route_scenario.py")
endif()
