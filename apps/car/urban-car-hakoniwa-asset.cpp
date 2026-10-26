#include "config/asset_manifest.hpp"
#include "hakoniwa/robot_runtime/factory/manifest_factory.hpp"
#include "physics/physics_impl.hpp"

#if HAKO_URBAN_ENABLE_VIEWER
#include "viewer/mujoco_viewer.hpp"
#include <GLFW/glfw3.h>
#endif

#include <atomic>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace {

struct Options {
    std::string manifest;
    std::string asset_name;
    std::string endpoint_name {"urban_car_runtime"};
    bool viewer {true};
    bool owns_conductor {true};
    bool view_model_only {false};
    bool validate_model_only {false};
    std::uint64_t realtime_sync_cycle_msec {2};
    /** The local Conductor's max_delay (with its own Conductor only). */
    std::uint64_t conductor_max_delay_msec {100};
    /** Lockstep inputs ("robot/pdu"): each step waits for a new command. */
    std::vector<std::string> lockstep_inputs;
    /** Vehicles driven by CarService/AckermannStep/<vehicle> instead of PDU. */
    std::vector<std::string> ackermann_rpc_inputs;
    /** People driven by PersonService/PersonStep/<person> instead of PDU. */
    std::vector<std::string> person_rpc_inputs;
    std::string rpc_service_config;
    /** Physics steps per Hakoniwa step (inputs once, state once per Hakoniwa step). */
    std::uint32_t physics_substeps {1};
};

void usage(const char* program)
{
    std::cerr
        << "Usage: " << program << " --manifest PATH [options]\n"
        << "  --asset-name NAME       override manifest asset name\n"
        << "  --endpoint-name NAME    endpoint instance name\n"
        << "  --no-viewer             run as a headless Hakoniwa asset\n"
        << "  --external-conductor    join a Conductor owned by another asset\n"
        << "  --realtime-sync-cycle-msec N\n"
        << "                          wall-clock sync cycle (0 disables)\n"
        << "  --conductor-max-delay-msec N\n"
        << "                          how far world time may run ahead of the slowest\n"
        << "                          asset (own Conductor only; default 100)\n"
        << "  --physics-substeps N    MuJoCo steps per Hakoniwa step (default 1)\n"
        << "  --lockstep-input ROBOT/PDU\n"
        << "                          step only when a new command has arrived on this\n"
        << "                          Ackermann input (repeatable; repeatable runs)\n"
#if HAKO_ENABLE_ASYNC_RPC_INPUT
        << "  --ackermann-rpc-input VEHICLE\n"
        << "                          use CarService/AckermannStep/<VEHICLE> (repeatable)\n"
        << "  --person-rpc-input PERSON\n"
        << "                          use PersonService/PersonStep/<PERSON> (repeatable)\n"
        << "  --rpc-service-config PATH\n"
        << "                          Core service definition JSON for RPC inputs\n"
#endif
        << "  --view-model            inspect the model without Hakoniwa\n"
        << "  --validate-model        load the XML/MJB and exit\n";
}

bool parse_options(int argc, char** argv, Options& options)
{
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--manifest" && index + 1 < argc) {
            options.manifest = argv[++index];
        } else if (argument == "--asset-name" && index + 1 < argc) {
            options.asset_name = argv[++index];
        } else if (argument == "--endpoint-name" && index + 1 < argc) {
            options.endpoint_name = argv[++index];
        } else if (argument == "--no-viewer") {
            options.viewer = false;
        } else if (argument == "--external-conductor") {
            options.owns_conductor = false;
        } else if (argument == "--realtime-sync-cycle-msec" && index + 1 < argc) {
            try {
                options.realtime_sync_cycle_msec = std::stoull(argv[++index]);
            } catch (const std::exception&) {
                std::cerr << "Invalid realtime sync cycle\n";
                return false;
            }
        } else if (argument == "--conductor-max-delay-msec" && index + 1 < argc) {
            try {
                options.conductor_max_delay_msec = std::stoull(argv[++index]);
            } catch (const std::exception&) {
                std::cerr << "Invalid Conductor max delay\n";
                return false;
            }
            if (options.conductor_max_delay_msec == 0) {
                std::cerr << "--conductor-max-delay-msec must be positive\n";
                return false;
            }
        } else if (argument == "--physics-substeps" && index + 1 < argc) {
            try {
                options.physics_substeps = static_cast<std::uint32_t>(std::stoul(argv[++index]));
            } catch (const std::exception&) {
                std::cerr << "Invalid physics substeps\n";
                return false;
            }
            if (options.physics_substeps == 0) {
                std::cerr << "--physics-substeps must be positive\n";
                return false;
            }
        } else if (argument == "--lockstep-input" && index + 1 < argc) {
            options.lockstep_inputs.emplace_back(argv[++index]);
#if HAKO_ENABLE_ASYNC_RPC_INPUT
        } else if (argument == "--ackermann-rpc-input" && index + 1 < argc) {
            options.ackermann_rpc_inputs.emplace_back(argv[++index]);
        } else if (argument == "--person-rpc-input" && index + 1 < argc) {
            options.person_rpc_inputs.emplace_back(argv[++index]);
        } else if (argument == "--rpc-service-config" && index + 1 < argc) {
            options.rpc_service_config = argv[++index];
#endif
        } else if (argument == "--view-model") {
            options.view_model_only = true;
        } else if (argument == "--validate-model") {
            options.validate_model_only = true;
        } else if (argument == "--help" || argument == "-h") {
            usage(argv[0]);
            return false;
        } else {
            std::cerr << "Unknown or incomplete argument: " << argument << '\n';
            usage(argv[0]);
            return false;
        }
    }
    if (options.manifest.empty()) {
        usage(argv[0]);
        return false;
    }
#if HAKO_ENABLE_ASYNC_RPC_INPUT
    if ((options.ackermann_rpc_inputs.empty()
            && options.person_rpc_inputs.empty())
        != options.rpc_service_config.empty()) {
        std::cerr << "RPC inputs and --rpc-service-config must be used together\n";
        return false;
    }
#endif
    return true;
}

std::shared_ptr<hako::robots::physics::impl::WorldImpl> load_model(
    const std::string& manifest_path)
{
    hako::robots::config::AssetManifest manifest;
    std::string error;
    if (!hako::robots::config::LoadAssetManifestFromJson(
            manifest_path, manifest, &error)) {
        std::cerr << "Failed to load manifest: " << error << '\n';
        return {};
    }
    auto world = std::make_shared<hako::robots::physics::impl::WorldImpl>();
    world->loadModel(manifest.model);
    return world;
}

int view_model(const std::string& manifest_path)
{
#if HAKO_URBAN_ENABLE_VIEWER
    auto world = load_model(manifest_path);
    if (world == nullptr) {
        return 1;
    }
    std::atomic_bool running {true};
    std::mutex mutex;
    MujocoRenderRuntime viewer(
        world->getModel(), world->getData(), running, mutex,
        MujocoRenderWindowMode::Visible);
    viewer.Run();
    return 0;
#else
    (void)manifest_path;
    std::cerr << "Viewer support is not compiled in\n";
    return 1;
#endif
}

int run_asset(const Options& options)
{
    auto config = hakoniwa::robot_runtime::factory::ManifestFactoryConfig {
        options.asset_name,
        options.endpoint_name,
        options.realtime_sync_cycle_msec,
        options.owns_conductor,
        options.conductor_max_delay_msec * 1000,
        options.lockstep_inputs,
        options.physics_substeps,
        options.ackermann_rpc_inputs,
        options.person_rpc_inputs,
        "UrbanPeople",
        options.rpc_service_config,
    };
    auto runner = hakoniwa::robot_runtime::factory::ManifestFactory::create(
        options.manifest, std::move(config));
    if (runner->start() != 0) {
        return 1;
    }

#if HAKO_URBAN_ENABLE_VIEWER
    if (options.viewer) {
        std::cout << "Controls: p=pause, r=reset, q/Esc=quit\n";
        MujocoRenderRuntime viewer(
            runner->model(), runner->data(), runner->running(),
            runner->model_mutex(), MujocoRenderWindowMode::Visible);
        viewer.SetKeyCallback([&runner](int key, int action, int) {
            if (action != GLFW_PRESS && action != GLFW_REPEAT) {
                return;
            }
            if (key == GLFW_KEY_ESCAPE || key == GLFW_KEY_Q) {
                runner->request_stop();
            } else if (key == GLFW_KEY_P) {
                runner->toggle_pause();
            } else if (key == GLFW_KEY_R) {
                runner->request_reset();
            }
        });
        viewer.Run();
        runner->request_stop();
    }
#else
    if (options.viewer) {
        std::cerr << "Viewer support is not compiled in\n";
        runner->request_stop();
    }
#endif
    return runner->wait();
}

} // namespace

int main(int argc, char** argv)
{
    Options options;
    if (!parse_options(argc, argv, options)) {
        return 2;
    }
    try {
        if (options.validate_model_only) {
            const auto world = load_model(options.manifest);
            if (world == nullptr) {
                return 1;
            }
            std::cout << "Validated MuJoCo model: " << options.manifest << '\n';
            return 0;
        }
        return options.view_model_only
            ? view_model(options.manifest)
            : run_asset(options);
    } catch (const std::exception& error) {
        std::cerr << "Urban Car runtime failed: " << error.what() << '\n';
        return 1;
    }
}
