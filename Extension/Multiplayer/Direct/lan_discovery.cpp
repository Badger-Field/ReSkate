#include "lan_discovery.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include <array>
#include <cstring>
#include <string_view>
#include <utility>

namespace dingosdk::multiplayer {
namespace {
constexpr std::string_view question = "RSKT?1", answer = "RSKT!1";
constexpr unsigned answers_per_second = 8;
constexpr std::intptr_t no_socket = -1;

#ifdef _WIN32
using Native = SOCKET;
void close_socket(std::intptr_t s) { closesocket(static_cast<Native>(s)); }
bool nonblocking(Native s) {
    u_long on = 1;
    return ioctlsocket(s, FIONBIO, &on) == 0;
}
#else
using Native = int;
void close_socket(std::intptr_t s) { ::close(static_cast<Native>(s)); }
bool nonblocking(Native s) { return fcntl(s, F_SETFL, fcntl(s, F_GETFL, 0) | O_NONBLOCK) == 0; }
#endif

// A UDP socket, nonblocking, bound to `port` (0: any) on every address.
std::intptr_t open_udp(std::uint16_t port, bool broadcast) {
    const auto s = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
#ifdef _WIN32
    if (s == INVALID_SOCKET) return no_socket;
#else
    if (s < 0) return no_socket;
#endif
    int on = 1;
    // Two copies of the game on one PC (a test) may both host.
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char *>(&on), sizeof(on));
    if (broadcast) setsockopt(s, SOL_SOCKET, SO_BROADCAST, reinterpret_cast<const char *>(&on), sizeof(on));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (::bind(s, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0 || !nonblocking(s)) {
        close_socket(static_cast<std::intptr_t>(s));
        return no_socket;
    }
    return static_cast<std::intptr_t>(s);
}

// One datagram, when one is waiting: its bytes and where it came from (host byte order).
bool receive(std::intptr_t s, std::string &bytes, std::uint32_t &from, std::uint16_t &from_port) {
    std::array<char, 1500> buffer{};
    sockaddr_in source{};
    socklen_t length = sizeof(source);
    const auto got = ::recvfrom(static_cast<Native>(s), buffer.data(), static_cast<int>(buffer.size()), 0,
                                reinterpret_cast<sockaddr *>(&source), &length);
    if (got <= 0 || source.sin_family != AF_INET) return false;
    bytes.assign(buffer.data(), static_cast<std::size_t>(got));
    from = ntohl(source.sin_addr.s_addr);
    from_port = ntohs(source.sin_port);
    return true;
}
void send_to(std::intptr_t s, std::string_view bytes, std::uint32_t to, std::uint16_t port) {
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_port = htons(port);
    target.sin_addr.s_addr = htonl(to);
    ::sendto(static_cast<Native>(s), bytes.data(), static_cast<int>(bytes.size()), 0, reinterpret_cast<sockaddr *>(&target),
             sizeof(target));
}
} // namespace

bool private_ipv4(std::uint32_t a) {
    return (a >> 24) == 10 || (a >> 20) == ((172u << 4) | 1) || (a >> 16) == ((192u << 8) | 168) ||
           (a >> 16) == ((169u << 8) | 254) || (a >> 24) == 127;
}

LanDiscovery::LanDiscovery(std::uint16_t port) : port_(port) {
#ifdef _WIN32
    WSADATA data{};
    network_ = WSAStartup(MAKEWORD(2, 2), &data) == 0;
#else
    network_ = true;
#endif
}
LanDiscovery::~LanDiscovery() {
    if (listener_ != no_socket) close_socket(listener_);
    if (asker_ != no_socket) close_socket(asker_);
#ifdef _WIN32
    if (network_) WSACleanup();
#endif
}

void LanDiscovery::announce(std::string row) {
    if (row.size() > max_row) row.clear();
    row_ = std::move(row);
    if (row_.empty()) {
        if (listener_ != no_socket) close_socket(std::exchange(listener_, no_socket));
        return;
    }
    if (listener_ == no_socket && network_) {
        listener_ = open_udp(port_, false);
        problem_ = listener_ == no_socket ? "LAN discovery: port " + std::to_string(port_) + " is unavailable." : std::string{};
    }
}

void LanDiscovery::search(std::uint32_t to) {
    found_.clear();
    if (!network_) return;
    if (asker_ == no_socket) asker_ = open_udp(0, true);
    if (asker_ != no_socket) send_to(asker_, question, to, port_);
}

void LanDiscovery::poll(std::uint64_t now_ms) {
    std::string bytes;
    std::uint32_t from{};
    std::uint16_t from_port{};
    if (now_ms - window_ >= 1000) {
        window_ = now_ms;
        answered_ = 0;
    }
    // Questions, while hosting: only from this network, and only a few answers a second.
    while (listener_ != no_socket && receive(listener_, bytes, from, from_port))
        if (bytes == question && private_ipv4(from) && !row_.empty() && answered_ < answers_per_second) {
            ++answered_;
            send_to(listener_, std::string(answer) + row_, from, from_port);
        }
    // Answers to this player's question, from this network only, each host once.
    while (asker_ != no_socket && receive(asker_, bytes, from, from_port)) {
        if (!private_ipv4(from) || bytes.size() <= answer.size() || bytes.compare(0, answer.size(), answer) != 0 ||
            bytes.size() - answer.size() > max_row || found_.size() >= 32)
            continue;
        auto row = bytes.substr(answer.size());
        bool seen = false;
        for (const auto &f : found_) seen = seen || f.row == row;
        if (!seen) found_.push_back({std::move(row), from});
    }
}
} // namespace dingosdk::multiplayer
