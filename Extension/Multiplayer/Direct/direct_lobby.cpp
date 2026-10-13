#include "direct_lobby.h"
#include "Engine/Core/Json/json.h"

#ifdef _WIN32
#include <Windows.h>
#include <winhttp.h>
#else
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <mutex>
#include <optional>
#include <thread>
#include <utility>

namespace dingosdk::multiplayer {
namespace {
using namespace std::chrono_literals;

constexpr std::size_t max_message = 64 * 1024, max_inbox = 256, max_outbox = 256;

struct Url {
    bool secure{};
    std::string host;
    std::uint16_t port{};
};
std::optional<Url> parse_url(std::string_view text) {
    Url url;
    if (text.starts_with("https://")) { url.secure = true; url.port = 443; text.remove_prefix(8); }
    else if (text.starts_with("http://")) { url.port = 80; text.remove_prefix(7); }
    else return std::nullopt;
    while (!text.empty() && text.back() == '/') text.remove_suffix(1);
    if (text.find('/') != std::string_view::npos || text.empty()) return std::nullopt;
    if (const auto colon = text.rfind(':'); colon != std::string_view::npos) {
        unsigned port = 0;
        for (const char c : text.substr(colon + 1)) {
            if (c < '0' || c > '9' || (port = port * 10 + static_cast<unsigned>(c - '0')) > 65535) return std::nullopt;
        }
        if (!port) return std::nullopt;
        url.port = static_cast<std::uint16_t>(port);
        text = text.substr(0, colon);
    }
    url.host = std::string(text);
    return url;
}

// What a request ended with: an HTTP status (0 when no answer came) and a word on why.
struct Failure {
    int status{};
    std::string why;
};

// One open WebSocket. send_text() and receive() may run on two threads at once; abort(),
// from any thread, makes a waiting receive() return.
class WebSocket {
  public:
    virtual ~WebSocket() = default;
    virtual bool send_text(std::string_view text) = 0;
    virtual std::optional<std::string> receive() = 0; // nothing: closed
    virtual void abort() = 0;
};

#ifdef _WIN32
// ---------------------------------------------------------------- WinHTTP (Windows)

std::wstring widen(std::string_view text) {
    std::wstring out(text.size(), L'\0');
    const auto n = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), out.data(), static_cast<int>(out.size()));
    out.resize(n > 0 ? static_cast<std::size_t>(n) : 0);
    return out;
}

struct Handle {
    HINTERNET h{};
    Handle() = default;
    explicit Handle(HINTERNET value) : h(value) {}
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
    Handle(Handle &&other) noexcept : h(std::exchange(other.h, nullptr)) {}
    Handle &operator=(Handle &&other) noexcept {
        if (this != &other) {
            if (h) WinHttpCloseHandle(h);
            h = std::exchange(other.h, nullptr);
        }
        return *this;
    }
    ~Handle() { if (h) WinHttpCloseHandle(h); }
    explicit operator bool() const { return h != nullptr; }
};

class Http {
  public:
    explicit Http(const Url &url) : url_(url) {
        session_.h = WinHttpOpen(widen(lobby_user_agent).c_str(), WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
        if (session_) {
            WinHttpSetTimeouts(session_.h, 10000, 10000, 10000, 10000);
            connection_.h = WinHttpConnect(session_.h, widen(url.host).c_str(), url.port, 0);
        }
    }
    // A request on this connection; `upgrade` asks for a WebSocket.
    std::optional<std::pair<Handle, int>> request(const std::string &path, bool upgrade, Failure &failure) {
        if (!connection_) { failure = {0, "cannot reach " + url_.host}; return std::nullopt; }
        Handle request(WinHttpOpenRequest(connection_.h, L"GET", widen(path).c_str(), nullptr, WINHTTP_NO_REFERER,
                                          WINHTTP_DEFAULT_ACCEPT_TYPES, url_.secure ? WINHTTP_FLAG_SECURE : 0));
        if (!request || (upgrade && !WinHttpSetOption(request.h, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET, nullptr, 0)) ||
            !WinHttpSendRequest(request.h, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
            !WinHttpReceiveResponse(request.h, nullptr)) {
            failure = {0, "no answer from " + url_.host + " (Windows error " + std::to_string(GetLastError()) + ")"};
            return std::nullopt;
        }
        DWORD status = 0, size = sizeof(status);
        WinHttpQueryHeaders(request.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX,
                            &status, &size, WINHTTP_NO_HEADER_INDEX);
        return std::pair<Handle, int>{std::move(request), static_cast<int>(status)};
    }
    std::optional<std::string> get(const std::string &path, Failure &failure) {
        auto response = request(path, false, failure);
        if (!response) return std::nullopt;
        std::string body;
        for (DWORD available = 0; WinHttpQueryDataAvailable(response->first.h, &available) && available;) {
            if (body.size() + available > max_message) { failure = {response->second, "answer too large"}; return std::nullopt; }
            const auto at = body.size();
            body.resize(at + available);
            DWORD read = 0;
            if (!WinHttpReadData(response->first.h, body.data() + at, available, &read)) break;
            body.resize(at + read);
        }
        if (response->second != 200) { failure = {response->second, "HTTP " + std::to_string(response->second)}; return std::nullopt; }
        return body;
    }

  private:
    Url url_;
    Handle session_, connection_;
    friend class WinWebSocket;
};

class WinWebSocket final : public WebSocket {
  public:
    WinWebSocket(std::unique_ptr<Http> http, HINTERNET socket) : http_(std::move(http)), socket_(socket) {}
    ~WinWebSocket() override { abort(); }
    bool send_text(std::string_view text) override {
        std::lock_guard lock(send_mutex_);
        return !aborted_ && WinHttpWebSocketSend(socket_.h, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
                                                 const_cast<char *>(text.data()), static_cast<DWORD>(text.size())) == NO_ERROR;
    }
    std::optional<std::string> receive() override {
        std::string message;
        char buffer[8192];
        for (;;) {
            DWORD read = 0;
            WINHTTP_WEB_SOCKET_BUFFER_TYPE type{};
            if (aborted_ || WinHttpWebSocketReceive(socket_.h, buffer, sizeof(buffer), &read, &type) != NO_ERROR ||
                type == WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE)
                return std::nullopt;
            message.append(buffer, read);
            if (message.size() > max_message) return std::nullopt;
            if (type == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE || type == WINHTTP_WEB_SOCKET_BINARY_MESSAGE_BUFFER_TYPE)
                return message;
        }
    }
    void abort() override {
        if (aborted_.exchange(true)) return;
        // Closing the handle ends a receive() waiting on it.
        WinHttpWebSocketShutdown(socket_.h, WINHTTP_WEB_SOCKET_SUCCESS_CLOSE_STATUS, nullptr, 0);
        std::lock_guard lock(send_mutex_);
        HINTERNET h = std::exchange(socket_.h, nullptr);
        if (h) WinHttpCloseHandle(h);
    }

  private:
    std::unique_ptr<Http> http_;
    Handle socket_;
    std::mutex send_mutex_;
    std::atomic<bool> aborted_{};
};

std::optional<std::string> http_get(const Url &url, const std::string &path, Failure &failure) {
    Http http(url);
    return http.get(path, failure);
}

std::unique_ptr<WebSocket> open_websocket(const Url &url, const std::string &path, Failure &failure) {
    auto http = std::make_unique<Http>(url);
    auto response = http->request(path, true, failure);
    if (!response) return nullptr;
    if (response->second != 101) { failure = {response->second, "HTTP " + std::to_string(response->second)}; return nullptr; }
    HINTERNET socket = WinHttpWebSocketCompleteUpgrade(response->first.h, 0);
    if (!socket) { failure = {101, "the WebSocket upgrade failed"}; return nullptr; }
    return std::make_unique<WinWebSocket>(std::move(http), socket);
}
#else
// ---------------------------------------------------------------- plain TCP (tests elsewhere)

int connect_tcp(const Url &url, Failure &failure) {
    if (url.secure) { failure = {0, "https needs Windows (WinHTTP) in this build"}; return -1; }
    addrinfo hints{}, *found{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(url.host.c_str(), std::to_string(url.port).c_str(), &hints, &found) != 0 || !found) {
        failure = {0, "cannot find " + url.host};
        return -1;
    }
    int fd = -1;
    for (auto *a = found; a && fd < 0; a = a->ai_next) {
        fd = ::socket(a->ai_family, a->ai_socktype, a->ai_protocol);
        if (fd >= 0 && ::connect(fd, a->ai_addr, a->ai_addrlen) != 0) { ::close(fd); fd = -1; }
    }
    freeaddrinfo(found);
    if (fd < 0) { failure = {0, "cannot connect to " + url.host}; return -1; }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    timeval timeout{10, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    return fd;
}

bool send_all(int fd, const void *data, std::size_t size) {
    auto *p = static_cast<const char *>(data);
    while (size) {
        const auto sent = ::send(fd, p, size, MSG_NOSIGNAL);
        if (sent <= 0) return false;
        p += sent;
        size -= static_cast<std::size_t>(sent);
    }
    return true;
}

// Sends the request and reads the response head; `rest` keeps what came after it.
std::optional<int> http_exchange(int fd, const Url &url, const std::string &path, const std::string &extra, std::string &rest,
                                 std::string &head) {
    const auto request = "GET " + path + " HTTP/1.1\r\nHost: " + url.host + ":" + std::to_string(url.port) + "\r\nUser-Agent: " +
                         lobby_user_agent + "\r\n" + extra + "\r\n";
    if (!send_all(fd, request.data(), request.size())) return std::nullopt;
    char buffer[4096];
    std::size_t end;
    while ((end = rest.find("\r\n\r\n")) == std::string::npos) {
        const auto got = ::recv(fd, buffer, sizeof(buffer), 0);
        if (got <= 0 || rest.size() > max_message) return std::nullopt;
        rest.append(buffer, static_cast<std::size_t>(got));
    }
    head = rest.substr(0, end);
    rest.erase(0, end + 4);
    if (head.size() < 12 || !head.starts_with("HTTP/1.1 ")) return std::nullopt;
    return std::atoi(head.c_str() + 9);
}

std::optional<std::string> http_get(const Url &url, const std::string &path, Failure &failure) {
    const int fd = connect_tcp(url, failure);
    if (fd < 0) return std::nullopt;
    std::string rest, head;
    const auto status = http_exchange(fd, url, path, "Connection: close\r\n", rest, head);
    char buffer[4096];
    for (ssize_t got; status && rest.size() <= max_message && (got = ::recv(fd, buffer, sizeof(buffer), 0)) > 0;)
        rest.append(buffer, static_cast<std::size_t>(got));
    ::close(fd);
    if (!status) { failure = {0, "no answer from " + url.host}; return std::nullopt; }
    if (*status != 200) { failure = {*status, "HTTP " + std::to_string(*status)}; return std::nullopt; }
    return rest;
}

class TcpWebSocket final : public WebSocket {
  public:
    TcpWebSocket(int fd, std::string rest) : fd_(fd), in_(std::move(rest)) {}
    ~TcpWebSocket() override { abort(); ::close(fd_); }
    bool send_text(std::string_view text) override { return send_frame(0x1, text); }
    std::optional<std::string> receive() override {
        std::string message;
        for (;;) {
            std::uint8_t opcode{};
            bool fin{};
            std::string payload;
            if (!read_frame(opcode, fin, payload)) return std::nullopt;
            if (opcode == 0x8) { send_frame(0x8, {}); return std::nullopt; } // close
            if (opcode == 0x9) { send_frame(0xA, payload); continue; }        // ping
            if (opcode == 0xA) continue;                                      // pong
            message += payload;
            if (message.size() > max_message) return std::nullopt;
            if (fin) return message;
        }
    }
    void abort() override {
        if (!aborted_.exchange(true)) ::shutdown(fd_, SHUT_RDWR);
    }

  private:
    int fd_;
    std::string in_;
    std::mutex send_mutex_;
    std::atomic<bool> aborted_{};

    bool send_frame(std::uint8_t opcode, std::string_view payload) {
        // A client masks what it sends (RFC 6455 5.3); the key need not be secret here.
        std::string frame;
        frame.push_back(static_cast<char>(0x80 | opcode));
        const auto size = payload.size();
        if (size < 126) frame.push_back(static_cast<char>(0x80 | size));
        else if (size < 65536) { frame.push_back(static_cast<char>(0x80 | 126)); frame.push_back(static_cast<char>(size >> 8)); frame.push_back(static_cast<char>(size & 255)); }
        else { frame.push_back(static_cast<char>(0x80 | 127)); for (int i = 7; i >= 0; --i) frame.push_back(static_cast<char>((size >> (i * 8)) & 255)); }
        static std::atomic<std::uint32_t> counter{0x9e3779b9u};
        const auto mask = counter.fetch_add(0x6d2b79f5u);
        for (int i = 3; i >= 0; --i) frame.push_back(static_cast<char>((mask >> (i * 8)) & 255));
        const auto *key = reinterpret_cast<const std::uint8_t *>(frame.data() + frame.size() - 4);
        std::string masked(payload);
        for (std::size_t i = 0; i < masked.size(); ++i) masked[i] = static_cast<char>(masked[i] ^ key[i % 4]);
        frame += masked;
        std::lock_guard lock(send_mutex_);
        return !aborted_ && send_all(fd_, frame.data(), frame.size());
    }
    bool fill(std::size_t want) {
        char buffer[8192];
        while (in_.size() < want) {
            const auto got = ::recv(fd_, buffer, sizeof(buffer), 0);
            if (got <= 0) return false;
            in_.append(buffer, static_cast<std::size_t>(got));
        }
        return true;
    }
    bool read_frame(std::uint8_t &opcode, bool &fin, std::string &payload) {
        if (!fill(2)) return false;
        const auto b0 = static_cast<std::uint8_t>(in_[0]), b1 = static_cast<std::uint8_t>(in_[1]);
        fin = b0 & 0x80;
        opcode = b0 & 0x0F;
        std::size_t head = 2;
        std::uint64_t size = b1 & 0x7F;
        if (size == 126) {
            if (!fill(4)) return false;
            size = static_cast<std::uint64_t>(static_cast<std::uint8_t>(in_[2])) << 8 | static_cast<std::uint8_t>(in_[3]);
            head = 4;
        } else if (size == 127) {
            if (!fill(10)) return false;
            size = 0;
            for (int i = 2; i < 10; ++i) size = size << 8 | static_cast<std::uint8_t>(in_[static_cast<std::size_t>(i)]);
            head = 10;
        }
        if (b1 & 0x80 || size > max_message) return false; // servers never mask
        if (!fill(head + size)) return false;
        payload = in_.substr(head, size);
        in_.erase(0, head + size);
        return true;
    }
};

std::unique_ptr<WebSocket> open_websocket(const Url &url, const std::string &path, Failure &failure) {
    const int fd = connect_tcp(url, failure);
    if (fd < 0) return nullptr;
    std::string rest, head;
    const auto status = http_exchange(fd, url, path,
                                      "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                                      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n",
                                      rest, head);
    if (!status || *status != 101) {
        ::close(fd);
        failure = {status.value_or(0), status ? "HTTP " + std::to_string(*status) : "no answer from " + url.host};
        return nullptr;
    }
    return std::make_unique<TcpWebSocket>(fd, std::move(rest));
}
#endif

// ---------------------------------------------------------------- the signaling channel

class LobbySignaling final : public DirectSignaling {
  public:
    LobbySignaling(std::string url, PlayerKey key)
        : url_text_(std::move(url)), key_(key), local_id_(player_id_of(key.public_key)) {
        problem_ = "Connecting to the lobby service...";
        worker_ = std::thread([this] { run(); });
    }
    ~LobbySignaling() override {
        {
            std::lock_guard lock(mutex_);
            stopping_ = true;
            if (socket_) socket_->abort();
        }
        wake_.notify_all();
        if (worker_.joinable()) worker_.join();
    }
    bool send(std::uint64_t to, std::span<const std::uint8_t> bytes) override {
        if (!to || bytes.empty() || bytes.size() > 16 * 1024) return false;
        std::lock_guard lock(mutex_);
        if (!ready_ || outbox_.size() >= max_outbox) return false;
        outbox_.push_back(signal_identity(to) + " " + to_hex(bytes));
        wake_.notify_all();
        return true;
    }
    std::vector<SignalMessage> receive() override {
        std::lock_guard lock(mutex_);
        return std::exchange(inbox_, {});
    }
    bool connected() const override {
        std::lock_guard lock(mutex_);
        return ready_;
    }
    std::string problem() const override {
        std::lock_guard lock(mutex_);
        return ready_ ? std::string{} : problem_;
    }
    bool needs_new_key() const override {
        std::lock_guard lock(mutex_);
        return key_taken_;
    }

  private:
    std::string url_text_;
    PlayerKey key_;
    std::uint64_t local_id_;
    std::thread worker_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    bool stopping_{}, ready_{}, key_taken_{};
    std::string problem_;
    std::shared_ptr<WebSocket> socket_;
    std::deque<std::string> outbox_;
    std::vector<SignalMessage> inbox_;

    void set_problem(std::string text) {
        std::lock_guard lock(mutex_);
        ready_ = false;
        problem_ = "Lobby service: " + std::move(text);
    }
    // Waits, unless the channel is closing; false when it is.
    bool pause(std::chrono::milliseconds time) {
        std::unique_lock lock(mutex_);
        return !wake_.wait_for(lock, time, [this] { return stopping_; });
    }

    void run() {
        const auto url = parse_url(url_text_);
        if (!url) { set_problem("\"" + url_text_ + "\" is not an http(s) address"); return; }
        auto backoff = 1s;
        while (pause(0ms)) {
            Failure failure;
            const bool reached = session(*url, failure);
            if (failure.status == 409) {
                std::lock_guard lock(mutex_);
                key_taken_ = true;
                ready_ = false;
                problem_ = "Lobby service: this player key's ID belongs to another key; a new key is needed.";
                return;
            }
            set_problem(failure.why.empty() ? "connection closed" : failure.why);
            // A session that got going starts the next attempt quickly; repeated failures back off.
            backoff = reached ? 1s : std::min<std::chrono::seconds>(backoff * 2, failure.status == 429 ? 300s : 30s);
            if (!pause(backoff)) return;
        }
    }

    // One connection, from challenge to close. True when it got as far as the service's hello.
    bool session(const Url &url, Failure &failure) {
        const auto body = http_get(url, "/v1/challenge", failure);
        if (!body) return false;
        std::string challenge;
        try {
            const auto json = Json::parse(*body);
            if (json.contains("challenge") && json.at("challenge").is_string()) challenge = json.at("challenge").string();
        } catch (...) {}
        if (challenge.empty() || challenge.size() > 256 ||
            !std::all_of(challenge.begin(), challenge.end(), [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '.'; })) {
            failure = {0, "the service sent no challenge"};
            return false;
        }
        const auto signature = sign(key_, "reskate-lobby-v1:" + challenge);
        const auto path = "/v1/connect?key=" + to_hex(key_.public_key) + "&sig=" + to_hex(signature) + "&challenge=" + challenge;
        auto socket = std::shared_ptr<WebSocket>(open_websocket(url, path, failure));
        if (!socket) return false;
        {
            std::lock_guard lock(mutex_);
            if (stopping_) return false;
            socket_ = socket;
        }
        // The writer sends what send() queued, and a ping now and then so idle connections stay up.
        std::atomic<bool> done{};
        std::thread writer([&] {
            auto next_ping = std::chrono::steady_clock::now() + 30s;
            for (;;) {
                std::string text;
                {
                    std::unique_lock lock(mutex_);
                    wake_.wait_until(lock, next_ping, [&] { return stopping_ || done || !outbox_.empty(); });
                    if (stopping_ || done) return;
                    if (!outbox_.empty()) { text = std::move(outbox_.front()); outbox_.pop_front(); }
                }
                if (text.empty()) { text = R"({"op":"ping"})"; next_ping = std::chrono::steady_clock::now() + 30s; }
                if (!socket->send_text(text)) { socket->abort(); return; }
            }
        });
        bool greeted = false;
        while (auto message = socket->receive()) {
            if (message->starts_with("steamid:")) {
                const auto space = message->find(' ');
                const auto from = space == std::string::npos ? 0 : signal_identity_id(std::string_view(*message).substr(0, space));
                auto hex = std::string_view(*message).substr(space == std::string::npos ? message->size() : space + 1);
                if (!from || hex.empty() || hex.size() % 2) continue;
                SignalMessage signal{from, {}};
                signal.bytes.reserve(hex.size() / 2);
                bool ok = true;
                for (std::size_t i = 0; i < hex.size() && ok; i += 2) {
                    const auto byte = from_hex<1>(hex.substr(i, 2));
                    ok = byte.has_value();
                    if (ok) signal.bytes.push_back((*byte)[0]);
                }
                std::lock_guard lock(mutex_);
                if (ok && inbox_.size() < max_inbox) inbox_.push_back(std::move(signal));
                continue;
            }
            try {
                const auto json = Json::parse(*message);
                const auto op = json.contains("op") && json.at("op").is_string() ? json.at("op").string() : std::string{};
                if (op == "hello") {
                    const auto id = json.contains("id") && json.at("id").is_string() ? json.at("id").string() : std::string{};
                    std::lock_guard lock(mutex_);
                    if (id != std::to_string(local_id_)) {
                        problem_ = "Lobby service: it names this player " + id + ", not " + std::to_string(local_id_);
                        break;
                    }
                    greeted = ready_ = true;
                    problem_.clear();
                } else if (op == "error" && json.contains("error") && json.at("error").is_string()) {
                    std::lock_guard lock(mutex_);
                    problem_ = "Lobby service: " + json.at("error").string();
                }
            } catch (...) {}
        }
        {
            std::lock_guard lock(mutex_);
            done = true;
            ready_ = false;
            socket_.reset();
        }
        socket->abort();
        wake_.notify_all();
        writer.join();
        if (failure.why.empty()) failure.why = greeted ? "connection lost" : "the service closed the connection";
        return greeted;
    }
};
} // namespace

std::unique_ptr<DirectSignaling> make_lobby_signaling(std::string url, PlayerKey key) {
    return std::make_unique<LobbySignaling>(std::move(url), key);
}
} // namespace dingosdk::multiplayer
