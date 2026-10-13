// Direct multiplayer end to end on one machine: two processes, a host and a guest, each running
// the real SteamTransport on the standalone GameNetworkingSockets library (open_direct), find
// each other with ICE through a signaling server this test runs (the protocol of
// GameNetworkingSockets' trivial signaling server), connect, and trade reliable and unreliable
// messages both ways.
//
//   dingosdk_direct_transport_tests <path to libGameNetworkingSockets.so>
#include "Extension/Multiplayer/Direct/direct_signaling.h"
#include "Extension/Multiplayer/Steam/steam_transport.h"

#include <arpa/inet.h>
#include <dlfcn.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using namespace dingosdk::multiplayer;
using namespace std::chrono_literals;

namespace {
constexpr std::uint64_t host_id = direct_player_id(0x1234), guest_id = direct_player_id(0xabcd);
static_assert(is_direct_player_id(host_id) && !is_direct_player_id(76561197960287930ull)); // a real (public) Steam ID

// The trivial signaling server: the first line names the client, then "<to> <hex>" lines go
// to that client as "<from> <hex>".
class SignalingServer {
  public:
    std::uint16_t start() {
        listener_ = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) || listen(listener_, 8)) return 0;
        socklen_t length = sizeof(address);
        getsockname(listener_, reinterpret_cast<sockaddr *>(&address), &length);
        accept_thread_ = std::thread([this] {
            for (;;) {
                const int client = accept(listener_, nullptr, nullptr);
                if (client < 0) return;
                std::thread([this, client] { serve(client); }).detach();
            }
        });
        return ntohs(address.sin_port);
    }
    void stop() {
        shutdown(listener_, SHUT_RDWR);
        close(listener_);
        if (accept_thread_.joinable()) accept_thread_.join();
    }
    unsigned forwarded() const { return forwarded_; }

  private:
    int listener_ = -1;
    std::thread accept_thread_;
    std::mutex mutex_;
    std::map<std::string, int> clients_;
    std::atomic<unsigned> forwarded_{};
    void serve(int client) {
        std::string buffer, name;
        char chunk[4096];
        for (;;) {
            const auto got = recv(client, chunk, sizeof(chunk), 0);
            if (got <= 0) break;
            buffer.append(chunk, static_cast<std::size_t>(got));
            for (std::size_t end; (end = buffer.find('\n')) != std::string::npos;) {
                const auto line = buffer.substr(0, end);
                buffer.erase(0, end + 1);
                if (name.empty()) {
                    name = line;
                    std::lock_guard lock(mutex_);
                    clients_[name] = client;
                    continue;
                }
                const auto space = line.find(' ');
                if (space == std::string::npos) continue;
                const auto out = name + line.substr(space) + "\n";
                std::lock_guard lock(mutex_);
                if (const auto to = clients_.find(line.substr(0, space)); to != clients_.end()) {
                    send(to->second, out.data(), out.size(), MSG_NOSIGNAL);
                    ++forwarded_;
                }
            }
        }
        std::lock_guard lock(mutex_);
        if (!name.empty() && clients_[name] == client) clients_.erase(name);
        close(client);
    }
};

int fail(const char *who, const std::string &why) {
    std::fprintf(stderr, "[%s] FAIL: %s\n", who, why.c_str());
    return 1;
}

bool wait_for(SteamTransport &transport, std::chrono::milliseconds limit, auto &&done) {
    const auto until = std::chrono::steady_clock::now() + limit;
    while (std::chrono::steady_clock::now() < until) {
        transport.poll();
        if (done()) return true;
        std::this_thread::sleep_for(5ms);
    }
    return false;
}

std::vector<std::uint8_t> bytes(const std::string &text) { return {text.begin(), text.end()}; }
std::string text(const std::vector<std::uint8_t> &data) { return {data.begin(), data.end()}; }

// One side of the test, in its own process (the library runs once a process, as one identity).
int run_side(bool hosting, const char *library_path, std::uint16_t port) {
    const char *who = hosting ? "host" : "guest";
    void *library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!library) return fail(who, std::string("cannot load the library: ") + dlerror());
    const auto local = hosting ? host_id : guest_id, peer = hosting ? guest_id : host_id;
    auto signaling = make_tcp_signaling("127.0.0.1", port, local);
    SteamTransport transport;
    // No STUN here: both sides are on this machine, so their own addresses are enough.
    if (!transport.open_direct(library, local, signaling.get(), ""))
        return fail(who, "open_direct: " + transport.status().detail);
    if (!transport.direct() || transport.status().local_id != local) return fail(who, "not in direct mode as the right player");
    if (hosting ? !transport.host(4) : !transport.join(peer)) return fail(who, "host/join: " + transport.status().detail);

    const auto connected = [&] {
        for (const auto &p : transport.status().peers)
            if (p.id == peer && p.connected) return true;
        return false;
    };
    transport.set_steam_debug(std::getenv("DIRECT_DEBUG") != nullptr);
    if (!wait_for(transport, 20s, connected)) {
        for (const auto &line : transport.take_direct_notes()) std::fprintf(stderr, "[%s] %s\n", who, line.c_str());
        return fail(who, "no connection: " + transport.status().detail);
    }
    std::printf("[%s] connected to %llx\n", who, static_cast<unsigned long long>(peer));

    // Reliable both ways: the guest greets, the host answers.
    std::vector<TransportMessage> got;
    const auto receive_text = [&](const std::string &expected) {
        return wait_for(transport, 10s, [&] {
            for (auto &m : transport.receive()) got.push_back(std::move(m));
            for (const auto &m : got)
                if (m.peer == peer && text(m.bytes) == expected) return true;
            return false;
        });
    };
    if (!hosting) {
        const auto hello = bytes("hello from the guest");
        if (!transport.send(peer, hello, true)) return fail(who, "send failed");
        if (!receive_text("hello back from the host")) return fail(who, "no answer from the host");
    } else {
        if (!receive_text("hello from the guest")) return fail(who, "no greeting from the guest");
        if (!transport.send(peer, bytes("hello back from the host"), true)) return fail(who, "send failed");
    }
    // Unreliable, on the gameplay lane: of 50 sent, most should arrive on a loopback.
    unsigned arrived{};
    for (int i = 0; i < 50; ++i) {
        transport.send(peer, bytes(std::string(hosting ? "h" : "g") + std::to_string(i)), false, false, TrafficLane::gameplay);
        transport.poll();
        std::this_thread::sleep_for(2ms);
    }
    wait_for(transport, 3s, [&] {
        for (auto &m : transport.receive())
            if (m.peer == peer && !m.bytes.empty() && m.bytes[0] == (hosting ? 'g' : 'h')) ++arrived;
        return arrived >= 50;
    });
    if (arrived < 40) return fail(who, "only " + std::to_string(arrived) + " of 50 unreliable messages arrived");
    std::printf("[%s] %u of 50 unreliable messages arrived\n", who, arrived);
    // Let the last of the other side's traffic out before closing.
    wait_for(transport, 1s, [] { return false; });
    transport.stop();
    std::printf("[%s] ok\n", who);
    return 0;
}
} // namespace

int main(int argc, char **argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: %s <libGameNetworkingSockets.so>\n", argv[0]);
        return 2;
    }
    if (signal_identity_id(signal_identity(host_id)) != host_id || signal_identity_id("steamid:12x") || signal_identity_id("str:1"))
        return fail("test", "identity strings");
    SignalingServer server;
    const auto port = server.start();
    if (!port) return fail("test", "signaling server did not start");
    pid_t sides[2]{};
    for (int i = 0; i < 2; ++i) {
        sides[i] = fork();
        if (sides[i] == 0) {
            const int result = run_side(i == 0, argv[1], port);
            std::fflush(nullptr);
            _exit(result);
        }
        if (i == 0) std::this_thread::sleep_for(300ms); // the host listens first
    }
    int failures = 0;
    for (const auto pid : sides) {
        int status = 0;
        waitpid(pid, &status, 0);
        failures += !(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    }
    const auto forwarded = server.forwarded();
    server.stop();
    if (!forwarded) return fail("test", "no signals went through the signaling server");
    std::printf("%s: %u signals relayed\n", failures ? "FAILED" : "passed", forwarded);
    return failures ? 1 : 0;
}
