#include "direct_lobby_api.h"
#include "Engine/Core/Json/json.h"

#include <charconv>
#include <map>
#include <optional>
#include <stdexcept>

namespace dingosdk::multiplayer {
namespace {
constexpr std::uint64_t republish_ms = 60'000;

class DirectLobbyApi final : public LobbyApi {
  public:
    explicit DirectLobbyApi(LobbyChannel channel) : ch_(std::move(channel)) {}

    void open() override {
        if (!ch_.ready || !ch_.ready())
            throw std::runtime_error("Not connected to the ReSkate lobby service yet. Try again in a moment.");
    }
    std::uint64_t create(unsigned) override {
        own_ = ch_.local_id();
        own_data_.clear();
        hosted_ = false;
        return finished(LobbyCall::create, {true, own_, 0, 1});
    }
    std::uint64_t search() override {
        if (list_call_ || !ch_.request(R"({"op":"list"})")) return 0;
        list_call_ = next_++;
        return *list_call_;
    }
    std::uint64_t join(std::uint64_t lobby) override {
        // Joining is connecting to the host; the listing only has to still be there.
        return finished(LobbyCall::join, {find(lobby) != nullptr, lobby, 0, 1});
    }
    std::optional<LobbyResult> poll(std::uint64_t call, LobbyCall kind) override {
        take_replies();
        const auto done = done_.find(call);
        if (done == done_.end() || done->second.first != kind) return std::nullopt;
        const auto result = done->second.second;
        done_.erase(done);
        return result;
    }
    void leave(std::uint64_t lobby) override {
        if (!lobby || lobby != own_) return;
        if (hosted_) ch_.request(R"({"op":"unhost"})");
        hosted_ = false;
        own_ = 0;
        own_data_.clear();
    }
    std::uint64_t at(int index) override {
        return index >= 0 && static_cast<std::size_t>(index) < rows_.size() ? rows_[static_cast<std::size_t>(index)].host : 0;
    }
    std::uint64_t owner(std::uint64_t lobby) override {
        // Asked about once a second while hosting: the listing is kept fresh from here.
        if (lobby && lobby == own_) {
            if (hosted_ && ch_.now_ms() - published_ >= republish_ms) publish();
            return own_;
        }
        return find(lobby) ? lobby : 0;
    }
    std::string data(std::uint64_t lobby, const char *key) override {
        const auto &values = lobby && lobby == own_ ? own_data_ : [&]() -> const std::map<std::string, std::string> & {
            static const std::map<std::string, std::string> none;
            const auto *row = find(lobby);
            return row ? row->data : none;
        }();
        const auto found = values.find(key);
        return found == values.end() ? std::string{} : found->second;
    }
    bool data(std::uint64_t lobby, const char *key, const std::string &value) override {
        if (!lobby || lobby != own_) return false;
        own_data_[key] = value;
        return true;
    }
    bool joinable(std::uint64_t lobby, bool open) override {
        if (!lobby || lobby != own_) return false;
        if (open) return publish();
        if (hosted_ && !ch_.request(R"({"op":"unhost"})")) return false;
        hosted_ = false;
        return true;
    }
    bool visibility(std::uint64_t lobby, bool everyone) override {
        if (!lobby || lobby != own_) return false;
        everyone_ = everyone;
        return true;
    }
    std::string name() override { return ch_.name ? ch_.name() : std::string{}; }
    std::string service() override { return "the lobby service"; }

  private:
    struct Row {
        std::uint64_t host{};
        std::map<std::string, std::string> data;
    };
    LobbyChannel ch_;
    std::uint64_t next_ = 1;
    std::map<std::uint64_t, std::pair<LobbyCall, LobbyResult>> done_;
    std::optional<std::uint64_t> list_call_;
    std::vector<Row> rows_;
    std::uint64_t own_{}, published_{};
    std::map<std::string, std::string> own_data_;
    bool everyone_ = true, hosted_{};

    std::uint64_t finished(LobbyCall kind, LobbyResult result) {
        const auto call = next_++;
        if (done_.size() >= 64) done_.erase(done_.begin());
        done_[call] = {kind, result};
        return call;
    }
    const Row *find(std::uint64_t lobby) const {
        for (const auto &row : rows_)
            if (row.host == lobby) return &row;
        return nullptr;
    }
    bool publish() {
        auto lobby = Json::object();
        for (const auto &[key, value] : own_data_) lobby[key] = value;
        auto message = Json::object();
        message["op"] = "host";
        message["public"] = everyone_;
        message["lobby"] = std::move(lobby);
        if (!ch_.request(message.dump())) return false;
        hosted_ = true;
        published_ = ch_.now_ms();
        return true;
    }
    void take_replies() {
        for (const auto &text : ch_.replies()) {
            try {
                const auto reply = Json::parse(text);
                const auto op = reply.contains("op") && reply.at("op").is_string() ? reply.at("op").string() : std::string{};
                if (op == "lobbies" && list_call_) {
                    rows_.clear();
                    if (reply.contains("rows") && reply.at("rows").is_array())
                        for (const auto &item : reply.at("rows")) read_row(item);
                    done_[*std::exchange(list_call_, std::nullopt)] = {
                        LobbyCall::list, {true, 0, static_cast<std::uint32_t>(rows_.size()), 1}};
                } else if (op == "error" && list_call_) {
                    done_[*std::exchange(list_call_, std::nullopt)] = {LobbyCall::list, {false, 0, 0, 2}};
                }
            } catch (...) {
            }
        }
    }
    void read_row(const Json &item) {
        if (!item.is_object() || !item.contains("host") || !item.at("host").is_string() || rows_.size() >= 200) return;
        Row row;
        const auto &host = item.at("host").string();
        const auto parsed = std::from_chars(host.data(), host.data() + host.size(), row.host);
        if (parsed.ec != std::errc{} || parsed.ptr != host.data() + host.size() || !row.host) return;
        for (const auto &[key, value] : item.items())
            if (key != "host" && key != "home" && value.is_string()) row.data[key] = value.string();
        // On the asker's own network, as the service sees it.
        row.data["rs_nearby"] = item.contains("home") && item.at("home").is_boolean() && item.at("home").get<bool>() ? "1" : "0";
        rows_.push_back(std::move(row));
    }
};
} // namespace

std::unique_ptr<LobbyApi> make_direct_lobby_api(LobbyChannel channel) {
    return std::make_unique<DirectLobbyApi>(std::move(channel));
}
} // namespace dingosdk::multiplayer
