// Direct multiplayer end to end on one machine: two processes, a host and a guest, each running
// the real SteamTransport on the standalone GameNetworkingSockets library (open_direct), find
// each other with ICE through a signaling server this test runs (the protocol of
// GameNetworkingSockets' trivial signaling server), connect, and trade reliable and unreliable
// messages both ways.
//
//   dingosdk_direct_transport_tests <path to libGameNetworkingSockets.so> [lobby url]
//
// With a lobby url (a running `wrangler dev` of contrib/lobby-service), the two players sign in
// to the lobby service with keys of their own and signal through it instead.
#include "Extension/Multiplayer/Direct/direct_lobby.h"
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
constexpr std::uint64_t host_id = direct_player_id(0x1234), guest_id = direct_player_id(0xabcd),
                        intruder_id = direct_player_id(0x0bad);
constexpr std::uint64_t session_secret = 0x5e55105e55105e55ull;
// Lobby mode: each side's key, made before the processes split.
const char *lobby_url{};
PlayerKey host_key, guest_key, intruder_key;
static_assert(is_direct_player_id(host_id) && !is_direct_player_id(76561197960287930ull)); // a real (public) Steam ID

// The trivial signaling server: the first line names the client, then "<to> <hex>" lines go
// to that client as "<from> <hex>".
class SignalingServer {
  public:
    // Opens the port. No threads yet: the players' processes are forked after this, and a
    // process forked while other threads run may inherit locks no one will release.
    std::uint16_t open() {
        listener_ = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) || listen(listener_, 8)) return 0;
        socklen_t length = sizeof(address);
        getsockname(listener_, reinterpret_cast<sockaddr *>(&address), &length);
        return ntohs(address.sin_port);
    }
    int listener() const { return listener_; }
    void run() {
        accept_thread_ = std::thread([this] {
            for (;;) {
                const int client = accept(listener_, nullptr, nullptr);
                if (client < 0) return;
                std::lock_guard lock(mutex_);
                sockets_.push_back(client);
                workers_.emplace_back([this, client] { serve(client); });
            }
        });
    }
    // Ends every thread before the server goes away.
    void stop() {
        shutdown(listener_, SHUT_RDWR);
        close(listener_);
        if (accept_thread_.joinable()) accept_thread_.join();
        std::vector<std::thread> workers;
        {
            std::lock_guard lock(mutex_);
            for (const int client : sockets_) shutdown(client, SHUT_RDWR);
            workers.swap(workers_);
        }
        for (auto &worker : workers) worker.join();
        std::lock_guard lock(mutex_);
        for (const int client : sockets_) close(client);
        sockets_.clear();
    }
    unsigned forwarded() const { return forwarded_; }

  private:
    int listener_ = -1;
    std::thread accept_thread_;
    std::mutex mutex_;
    std::map<std::string, int> clients_;
    std::vector<int> sockets_;
    std::vector<std::thread> workers_;
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
                    // TCP may take only part of it; the whole line goes, or it is not counted.
                    std::size_t sent = 0;
                    while (sent < out.size()) {
                        const auto n = send(to->second, out.data() + sent, out.size() - sent, MSG_NOSIGNAL);
                        if (n <= 0) break;
                        sent += static_cast<std::size_t>(n);
                    }
                    if (sent == out.size()) ++forwarded_;
                }
            }
        }
        std::lock_guard lock(mutex_);
        if (!name.empty() && clients_[name] == client) clients_.erase(name);
        // stop() closes the socket, after this thread has finished with it.
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
std::unique_ptr<DirectSignaling> signaling_for(const PlayerKey &key, std::uint16_t port, std::uint64_t local) {
    if (!lobby_url) return make_tcp_signaling("127.0.0.1", port, local);
    auto signaling = make_lobby_signaling(lobby_url, key);
    for (int i = 0; i < 500 && !signaling->connected(); ++i) std::this_thread::sleep_for(20ms);
    return signaling;
}

int run_side(bool hosting, const char *library_path, std::uint16_t port) {
    const char *who = hosting ? "host" : "guest";
    void *library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!library) return fail(who, std::string("cannot load the library: ") + dlerror());
    const auto host = lobby_url ? player_id_of(host_key.public_key) : host_id;
    const auto guest = lobby_url ? player_id_of(guest_key.public_key) : guest_id;
    const auto local = hosting ? host : guest, peer = hosting ? guest : host;
    SteamTransport transport;
    // No STUN or TURN here: both sides are on this machine, so their own addresses are enough.
    auto signaling = signaling_for(hosting ? host_key : guest_key, port, local);
    // (The plain TCP channel connects on first use; the lobby one is up before the transport starts.)
    if (lobby_url && !signaling->connected()) return fail(who, "lobby: " + signaling->problem());
    if (!transport.open_direct(library, local, std::move(signaling), {}))
        return fail(who, "open_direct: " + transport.status().detail);
    if (!transport.direct() || transport.status().local_id != local) return fail(who, "not in direct mode as the right player");
    if (hosting ? !transport.host(4) : !transport.join(peer)) return fail(who, "host/join: " + transport.status().detail);
    // Both hold the session's join code, so both have its token.
    transport.set_admission_token(direct_join_token(session_secret));

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
// A player who knows the host's ID but not the session's code: their signals carry the wrong
// token, so the host's networking library never sees them and they never get a connection
// (nor learn the host's addresses), while the real guest does.
int run_intruder(const char *library_path, std::uint16_t port) {
    const char *who = "intruder";
    void *library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (!library) return fail(who, std::string("cannot load the library: ") + dlerror());
    const auto host = lobby_url ? player_id_of(host_key.public_key) : host_id;
    const auto local = lobby_url ? player_id_of(intruder_key.public_key) : intruder_id;
    SteamTransport transport;
    auto signaling = signaling_for(intruder_key, port, local);
    if (lobby_url && !signaling->connected()) return fail(who, "lobby: " + signaling->problem());
    if (!transport.open_direct(library, local, std::move(signaling), {}) || !transport.join(host))
        return fail(who, "could not start: " + transport.status().detail);
    transport.set_admission_token(direct_join_token(session_secret ^ 1));
    const bool connected = wait_for(transport, 6s, [&] {
        for (const auto &p : transport.status().peers)
            if (p.id == host && p.connected) return true;
        return false;
    });
    transport.stop();
    if (connected) return fail(who, "connected to the host without the session's code");
    std::printf("[%s] refused, as it should be\n", who);
    return 0;
}
} // namespace

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) {
        std::fprintf(stderr, "usage: %s <libGameNetworkingSockets.so> [lobby url]\n", argv[0]);
        return 2;
    }
    if (argc == 3) {
        lobby_url = argv[2];
        host_key = generate_player_key().value();
        guest_key = generate_player_key().value();
        intruder_key = generate_player_key().value();
    }
    if (signal_identity_id(signal_identity(host_id)) != host_id || signal_identity_id("steamid:12x") || signal_identity_id("str:1"))
        return fail("test", "identity strings");
    SignalingServer server;
    const auto port = server.open();
    if (!port) return fail("test", "signaling server did not start");
    pid_t sides[3]{};
    for (int i = 0; i < 3; ++i) {
        sides[i] = fork();
        if (sides[i] == 0) {
            close(server.listener());
            if (i > 0) std::this_thread::sleep_for(300ms); // the host listens first
            const int result = i == 2 ? run_intruder(argv[1], port) : run_side(i == 0, argv[1], port);
            std::fflush(nullptr);
            _exit(result);
        }
    }
    if (!lobby_url) server.run();
    int failures = 0;
    for (const auto pid : sides) {
        int status = 0;
        waitpid(pid, &status, 0);
        failures += !(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    }
    const auto forwarded = server.forwarded();
    if (lobby_url) {
        close(server.listener());
        std::printf("%s: through the lobby service at %s\n", failures ? "FAILED" : "passed", lobby_url);
        return failures ? 1 : 0;
    }
    server.stop();
    if (!forwarded) return fail("test", "no signals went through the signaling server");
    std::printf("%s: %u signals relayed\n", failures ? "FAILED" : "passed", forwarded);
    return failures ? 1 : 0;
}
