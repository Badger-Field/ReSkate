#include "direct_signaling.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include <charconv>
#include <future>
#include <chrono>
#include <cstring>
#include <utility>

namespace dingosdk::multiplayer {
namespace {
#ifdef _WIN32
using Socket = SOCKET;
constexpr Socket no_socket = INVALID_SOCKET;
constexpr int send_flags = 0;
void close_socket(Socket s) { closesocket(s); }
bool would_block() { const auto e = WSAGetLastError(); return e == WSAEWOULDBLOCK || e == WSAEINPROGRESS || e == WSAEALREADY; }
bool set_nonblocking(Socket s) { u_long on = 1; return ioctlsocket(s, FIONBIO, &on) == 0; }
#else
using Socket = int;
constexpr Socket no_socket = -1;
// A send to a connection the service has closed must not raise SIGPIPE, which ends the process.
constexpr int send_flags = MSG_NOSIGNAL;
void close_socket(Socket s) { ::close(s); }
bool would_block() { return errno == EWOULDBLOCK || errno == EAGAIN || errno == EINPROGRESS; }
bool set_nonblocking(Socket s) { const auto flags = fcntl(s, F_GETFL, 0); return flags >= 0 && fcntl(s, F_SETFL, flags | O_NONBLOCK) == 0; }
#endif

// A signal is a few KB at most (ICE candidates and a handshake); anything larger is refused
// both ways, and so is a peer that floods.
constexpr std::size_t max_signal = 16 * 1024, max_buffer = 256 * 1024, max_inbox = 256;
constexpr std::chrono::seconds retry_after{3};

std::uint64_t now_ms() {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

class TcpSignaling final : public DirectSignaling {
  public:
    TcpSignaling(std::string host, std::uint16_t port, std::uint64_t local_id)
        : host_(std::move(host)), port_(port), identity_(signal_identity(local_id)) {
#ifdef _WIN32
        WSADATA data{};
        started_ = WSAStartup(MAKEWORD(2, 2), &data) == 0;
#endif
    }
    ~TcpSignaling() override {
        drop("closed");
#ifdef _WIN32
        if (started_) WSACleanup();
#endif
    }
    bool send(std::uint64_t to, std::span<const std::uint8_t> bytes) override {
        if (!to || bytes.empty() || bytes.size() > max_signal) return false;
        pump();
        if (state_ != State::ready) return false;
        static constexpr char digits[] = "0123456789abcdef";
        std::string line = signal_identity(to);
        line.push_back(' ');
        line.reserve(line.size() + bytes.size() * 2 + 1);
        for (const auto b : bytes) {
            line.push_back(digits[b >> 4]);
            line.push_back(digits[b & 15]);
        }
        line.push_back('\n');
        if (out_.size() + line.size() > max_buffer) return false;
        out_ += line;
        pump();
        return true;
    }
    std::vector<SignalMessage> receive() override {
        pump();
        return std::exchange(inbox_, {});
    }
    bool connected() const override { return state_ == State::ready; }
    std::string problem() const override { return state_ == State::ready ? std::string{} : problem_; }

  private:
    enum class State { idle, resolving, connecting, ready };
    // A resolved address, copied out of getaddrinfo's list.
    struct Address {
        int family{}, type{}, protocol{};
        sockaddr_storage address{};
        socklen_t length{};
    };
    // The name lookup runs on its own thread: getaddrinfo blocks, and send() and receive()
    // must not hold up the game.
    std::future<std::vector<Address>> lookup_;
    std::string host_;
    std::uint16_t port_{};
    std::string identity_;
    Socket socket_ = no_socket;
    State state_ = State::idle;
    std::string in_, out_, problem_ = "Not connected to the lobby service yet.";
    std::vector<SignalMessage> inbox_;
    std::uint64_t retry_at_{};
#ifdef _WIN32
    bool started_{};
#endif

    void drop(const std::string &why) {
        if (socket_ != no_socket) close_socket(socket_);
        socket_ = no_socket;
        state_ = State::idle;
        in_.clear();
        out_.clear();
        problem_ = "Lobby service: " + why;
        retry_at_ = now_ms() + std::chrono::milliseconds(retry_after).count();
    }
    void start() {
        if (now_ms() < retry_at_) return;
        lookup_ = std::async(std::launch::async, [host = host_, port = port_] {
            std::vector<Address> out;
            addrinfo hints{}, *found{};
            hints.ai_family = AF_UNSPEC;
            hints.ai_socktype = SOCK_STREAM;
            if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &found) != 0 || !found) return out;
            for (auto *a = found; a && out.size() < 8; a = a->ai_next) {
                if (a->ai_addrlen > sizeof(sockaddr_storage)) continue;
                Address address{a->ai_family, a->ai_socktype, a->ai_protocol, {}, static_cast<socklen_t>(a->ai_addrlen)};
                std::memcpy(&address.address, a->ai_addr, a->ai_addrlen);
                out.push_back(address);
            }
            freeaddrinfo(found);
            return out;
        });
        state_ = State::resolving;
    }
    void connect_to(const std::vector<Address> &addresses) {
        if (addresses.empty()) { drop("cannot find " + host_); return; }
        for (const auto &a : addresses) {
            socket_ = ::socket(a.family, a.type, a.protocol);
            if (socket_ == no_socket) continue;
            if (!set_nonblocking(socket_)) { close_socket(socket_); socket_ = no_socket; continue; }
            int one = 1;
            setsockopt(socket_, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char *>(&one), sizeof(one));
            if (::connect(socket_, reinterpret_cast<const sockaddr *>(&a.address), static_cast<int>(a.length)) == 0 || would_block()) break;
            close_socket(socket_);
            socket_ = no_socket;
        }
        if (socket_ == no_socket) { drop("cannot connect to " + host_); return; }
        state_ = State::connecting;
        out_ = identity_ + "\n" + out_;
    }
    // Moves bytes both ways without blocking.
    void pump() {
        if (state_ == State::idle) start();
        if (state_ == State::resolving) {
            if (lookup_.wait_for(std::chrono::seconds(0)) != std::future_status::ready) return;
            connect_to(lookup_.get());
        }
        if (socket_ == no_socket) return;
        if (state_ == State::connecting) {
            fd_set writable;
            FD_ZERO(&writable);
            FD_SET(socket_, &writable);
            timeval none{};
            if (select(static_cast<int>(socket_ + 1), nullptr, &writable, nullptr, &none) <= 0) return;
            int error = 0;
            socklen_t length = sizeof(error);
            getsockopt(socket_, SOL_SOCKET, SO_ERROR, reinterpret_cast<char *>(&error), &length);
            if (error) { drop("cannot connect to " + host_); return; }
            state_ = State::ready;
            problem_.clear();
        }
        while (!out_.empty()) {
            const auto sent = ::send(socket_, out_.data(), static_cast<int>(out_.size()), send_flags);
            if (sent > 0) { out_.erase(0, static_cast<std::size_t>(sent)); continue; }
            if (sent < 0 && would_block()) break;
            drop("connection lost");
            return;
        }
        char buffer[8192];
        for (;;) {
            const auto got = ::recv(socket_, buffer, sizeof(buffer), 0);
            if (got > 0) {
                in_.append(buffer, static_cast<std::size_t>(got));
                if (in_.size() > max_buffer) { drop("the service sent too much"); return; }
                continue;
            }
            if (got < 0 && would_block()) break;
            drop(got == 0 ? "the service closed the connection" : "connection lost");
            return;
        }
        for (std::size_t end; (end = in_.find('\n')) != std::string::npos;) {
            const auto line = in_.substr(0, end);
            in_.erase(0, end + 1);
            parse(line);
        }
    }
    void parse(std::string_view line) {
        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        const auto space = line.find(' ');
        if (space == std::string_view::npos) return;
        const auto from = signal_identity_id(line.substr(0, space));
        auto hex = line.substr(space + 1);
        while (!hex.empty() && (hex.back() == ' ' || hex.back() == '\r')) hex.remove_suffix(1);
        if (!from || hex.empty() || hex.size() % 2 || hex.size() / 2 > max_signal || inbox_.size() >= max_inbox) return;
        SignalMessage message{from, {}};
        message.bytes.reserve(hex.size() / 2);
        for (std::size_t i = 0; i < hex.size(); i += 2) {
            const auto hi = hex_value(hex[i]), lo = hex_value(hex[i + 1]);
            if (hi < 0 || lo < 0) return;
            message.bytes.push_back(static_cast<std::uint8_t>(hi << 4 | lo));
        }
        inbox_.push_back(std::move(message));
    }
};
} // namespace

std::string signal_identity(std::uint64_t id) { return "steamid:" + std::to_string(id); }

std::uint64_t signal_identity_id(std::string_view text) {
    constexpr std::string_view prefix = "steamid:";
    if (!text.starts_with(prefix)) return 0;
    text.remove_prefix(prefix.size());
    std::uint64_t id{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), id);
    return error == std::errc{} && end == text.data() + text.size() ? id : 0;
}

std::unique_ptr<DirectSignaling> make_tcp_signaling(std::string host, std::uint16_t port, std::uint64_t local_id) {
    return std::make_unique<TcpSignaling>(std::move(host), port, local_id);
}
} // namespace dingosdk::multiplayer
