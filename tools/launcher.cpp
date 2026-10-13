#include <array>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <sys/wait.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef ATLAS_VERSION
#define ATLAS_VERSION "development"
#endif

namespace fs = std::filesystem;
static volatile sig_atomic_t running_child = -1;
static void forward_signal(int signal) {
    if (running_child > 0) kill(static_cast<pid_t>(running_child), signal);
}
static std::string variable(const char* name) {
    const char* value = std::getenv(name);
    return value ? value : "";
}
static int run(std::vector<std::string> arguments) {
    std::vector<char*> argv;
    for (auto& value : arguments) argv.push_back(value.data());
    argv.push_back(nullptr);
    pid_t child = fork();
    if (child < 0) return 127;
    if (child == 0) {
        execvp(argv[0], argv.data());
        std::cerr << "Atlas PDF: não foi possível iniciar " << arguments[0] << ": " << std::strerror(errno) << '\n';
        _exit(127);
    }
    running_child = child;
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) { running_child = -1; return 127; }
    }
    running_child = -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
struct Temporary {
    fs::path path;
    ~Temporary() { std::error_code error; if (!path.empty()) fs::remove_all(path, error); }
};
int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << "Atlas PDF " << ATLAS_VERSION << " · Categorias por conteúdo e estrutura · Linux x86_64\n";
            return 0;
        }
        std::array<char, 4096> location{};
        const auto length = readlink("/proc/self/exe", location.data(), location.size() - 1);
        if (length < 0) throw std::runtime_error("não foi possível localizar o executável");
        std::ifstream source(std::string(location.data(), static_cast<size_t>(length)), std::ios::binary);
        source.seekg(0, std::ios::end);
        const auto file_size = static_cast<std::uint64_t>(source.tellg());
        if (file_size < 24) throw std::runtime_error("pacote incompleto");
        source.seekg(static_cast<std::streamoff>(file_size - 24));
        std::array<unsigned char, 8> size_bytes{};
        source.read(reinterpret_cast<char*>(size_bytes.data()), 8);
        std::uint64_t payload_size = 0;
        for (int i = 0; i < 8; ++i) payload_size |= static_cast<std::uint64_t>(size_bytes[i]) << (8 * i);
        std::array<char, 16> magic{};
        source.read(magic.data(), magic.size());
        if (std::string(magic.data(), magic.size()) != "ATLASPDFARCHIVE1" || payload_size > file_size - 24)
            throw std::runtime_error("arquivo do aplicativo danificado");
        std::string pattern = (fs::temp_directory_path() / "atlas-pdf-XXXXXX").string();
        std::vector<char> writable(pattern.begin(), pattern.end());
        writable.push_back(0);
        char* created = mkdtemp(writable.data());
        if (!created) throw std::runtime_error("não foi possível preparar a pasta temporária");
        Temporary temporary{fs::path(created)};
        const auto archive = temporary.path / "payload.tar.gz";
        std::ofstream output(archive, std::ios::binary);
        source.seekg(static_cast<std::streamoff>(file_size - 24 - payload_size));
        std::array<char, 1024 * 256> buffer{};
        while (payload_size) {
            auto amount = static_cast<std::streamsize>(std::min<std::uint64_t>(payload_size, buffer.size()));
            if (!source.read(buffer.data(), amount)) throw std::runtime_error("pacote truncado");
            output.write(buffer.data(), amount);
            payload_size -= static_cast<std::uint64_t>(amount);
        }
        output.close();
        if (!output) throw std::runtime_error("espaço insuficiente para abrir o aplicativo");
        std::signal(SIGINT, forward_signal);
        std::signal(SIGTERM, forward_signal);
        if (run({"tar", "-xzf", archive.string(), "-C", temporary.path.string()}) != 0)
            throw std::runtime_error("falha ao extrair o aplicativo (tar/gzip necessários no sistema)");
        fs::remove(archive);
        const std::string root = temporary.path.string();
        const std::string original_path = variable("PATH");
        const std::string original_ld = variable("LD_LIBRARY_PATH");
        setenv("ATLAS_ORIGINAL_PATH", original_path.c_str(), 1);
        setenv("ATLAS_ORIGINAL_LD_LIBRARY_PATH", original_ld.c_str(), 1);
        setenv("ATLAS_RESOURCE_DIR", root.c_str(), 1);
        setenv("ATLAS_PYTHON", (root + "/python/bin/python3").c_str(), 1);
        setenv("PATH", (root + "/bin:" + original_path).c_str(), 1);
        setenv("LD_LIBRARY_PATH", (root + "/lib").c_str(), 1);
        setenv("PYTHONHOME", (root + "/python").c_str(), 1);
        unsetenv("PYTHONPATH");
        setenv("PYTHONNOUSERSITE", "1", 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("QT_PLUGIN_PATH", (root + "/plugins").c_str(), 1);
        setenv("QT_QPA_PLATFORM_PLUGIN_PATH", (root + "/plugins/platforms").c_str(), 1);
        unsetenv("QT_QPA_PLATFORMTHEME");
        unsetenv("QT_STYLE_OVERRIDE");
        if (variable("QT_QPA_PLATFORM").empty() && !variable("DISPLAY").empty())
            setenv("QT_QPA_PLATFORM", "xcb", 1);
        std::vector<std::string> command;
        int first_argument = 1;
        if (argc > 1 && std::string(argv[1]) == "--backend") {
            command = {root + "/python/bin/python3", root + "/backend/indexer.py"};
            first_argument = 2;
        } else if (fs::exists(temporary.path / "electron/electron")) {
            // A copied SUID helper loses root ownership. Reuse an installed,
            // root-owned helper if available; otherwise use user namespaces.
            const auto sandbox = temporary.path / "electron/chrome-sandbox";
            fs::remove(sandbox);
            for (const char* helper : {"/opt/google/chrome/chrome-sandbox", "/usr/lib/chromium/chrome-sandbox", "/usr/lib/chromium-browser/chrome-sandbox"}) {
                struct stat info{};
                if (stat(helper, &info) == 0 && S_ISREG(info.st_mode) && info.st_uid == 0 && (info.st_mode & S_ISUID) && (info.st_mode & S_IXOTH)) {
                    fs::create_symlink(helper, sandbox);
                    break;
                }
            }
            setenv("ATLAS_BACKEND_LIBRARY_PATH", (root + "/lib").c_str(), 1);
            setenv("ATLAS_BACKEND_PYTHONHOME", (root + "/python").c_str(), 1);
            setenv("ATLAS_BACKEND_PATH", (root + "/bin:" + original_path).c_str(), 1);
            setenv("PATH", original_path.c_str(), 1);
            if (original_ld.empty()) unsetenv("LD_LIBRARY_PATH");
            else setenv("LD_LIBRARY_PATH", original_ld.c_str(), 1);
            unsetenv("PYTHONHOME");
            unsetenv("QT_PLUGIN_PATH");
            unsetenv("QT_QPA_PLATFORM_PLUGIN_PATH");
            command = {root + "/electron/electron", root};
        } else command = {root + "/bin/atlas-pdf-ui"};
        bool explicit_data = !variable("ATLAS_DATA_DIR").empty();
        for (int i = first_argument; i < argc; ++i) {
            const std::string argument(argv[i]);
            if (argument == "--data-dir" || argument.rfind("--data-dir=", 0) == 0) explicit_data = true;
        }
        // A desktop copy sits beside the existing project catalog. Double-click
        // and the desktop shortcut should open the same library.
        const auto adjacent_data = fs::path(std::string(location.data(), static_cast<size_t>(length)))
                                       .parent_path() / "atlas-pdf/data";
        if (!explicit_data && fs::is_regular_file(adjacent_data / "catalog.sqlite3")) {
            command.emplace_back("--data-dir");
            command.push_back(adjacent_data.string());
        }
        for (int i = first_argument; i < argc; ++i) command.emplace_back(argv[i]);
        return run(std::move(command));
    } catch (const std::exception& error) {
        std::cerr << "Atlas PDF: " << error.what() << '\n';
        return 1;
    }
}
