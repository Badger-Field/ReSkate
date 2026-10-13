// Lobbies without Steam (direct_lobby_api.h) under the browser's own lifecycle (SteamLobbies),
// against a stand-in for the lobby service: a host's code-only game is listed on their home
// network, a player there finds it in the list and joins it; it stays listed while the host
// plays and goes when they stop.
#include "Extension/Multiplayer/Direct/direct_lobby_api.h"
#include "Extension/Multiplayer/Direct/direct_signaling.h"
#include "Engine/Core/Json/json.h"

#include <cstdio>
#include <map>
#include <string>
#include <vector>

using namespace dingosdk;
using namespace dingosdk::multiplayer;

namespace {
int failures = 0;
void check(bool ok, const char *what) {
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", what);
    }
}

// The service: who is listed, publicly or on which home network.
struct Service {
    struct Listing {
        Json lobby;
        bool everyone{};
        std::string home;
    };
    std::map<std::uint64_t, Listing> listings;
    int hosts{}, unhosts{};
};
struct Player {
    Service &service;
    std::uint64_t id;
    std::string home;
    std::vector<std::string> answers;
    std::uint64_t clock = 1000;
    bool connected = true;
    LobbyChannel channel() {
        LobbyChannel c;
        c.request = [this](std::string_view text) {
            if (!connected) return false;
            const auto request = Json::parse(text);
            const auto op = request.at("op").string();
            if (op == "host") {
                service.listings[id] = {request.at("lobby"), request.at("public").get<bool>(), home};
                ++service.hosts;
                answers.push_back(R"({"op":"hosted"})");
            } else if (op == "unhost") {
                service.listings.erase(id);
                ++service.unhosts;
                answers.push_back(R"({"op":"unhosted"})");
            } else if (op == "list") {
                auto rows = Json::array();
                for (const auto &[host, listing] : service.listings) {
                    if (!listing.everyone && listing.home != home) continue;
                    auto row = listing.lobby;
                    row["host"] = std::to_string(host);
                    row["home"] = listing.home == home;
                    rows.push_back(row);
                }
                auto reply = Json::object();
                reply["op"] = "lobbies";
                reply["rows"] = rows;
                answers.push_back(reply.dump());
            }
            return true;
        };
        c.replies = [this] { return std::exchange(answers, {}); };
        c.local_id = [this] { return id; };
        c.name = [] { return std::string("Dad"); };
        c.now_ms = [this] { return clock; };
        return c;
    }
};
} // namespace

int main() {
    Service service;
    const auto dad_id = direct_player_id(0x00c0ffee), son_id = direct_player_id(0x0000beef),
               stranger_id = direct_player_id(0x00000bad);
    Player dad{service, dad_id, "home"}, son{service, son_id, "home"}, stranger{service, stranger_id, "elsewhere"};
    SteamLobbies hosting(make_direct_lobby_api(dad.channel()));
    SteamLobbies browsing(make_direct_lobby_api(son.channel()));
    SteamLobbies away(make_direct_lobby_api(stranger.channel()));

    // Dad hosts with a code: listed for his home network, not publicly. His connection to the
    // service is still opening when he starts; the listing goes out once it is there.
    const auto code = format_invite({dad_id, 0x0123456789abcdefull});
    std::uint64_t now = 1'000'000;
    dad.connected = false;
    hosting.host(code, 8, false, "Dad's park", false);
    hosting.update_host(true, 1, "levels/park/bam", now);
    hosting.tick(now += 200'000);
    hosting.update_host(true, 1, "levels/park/bam", now += 1'100'000);
    check(!service.listings.contains(dad_id), "nothing is listed before the connection is there");
    dad.connected = true;
    hosting.update_host(true, 1, "levels/park/bam", now += 1'100'000);
    check(service.listings.contains(dad_id) && !service.listings[dad_id].everyone, "the game is listed, not publicly");
    check(hosting.status().listed && hosting.hosted_lobby() == dad_id, "the host sees it listed");
    check(hosting.status().hosting == "Listed for players on your home network.", "the host is told where it is listed");

    // His son, on the same network, finds it and joins from the list.
    browsing.refresh(now);
    browsing.tick(now += 200'000);
    const auto &rows = browsing.status().rows;
    check(rows.size() == 1 && rows[0].owner == dad_id && rows[0].name == "Dad's park" && rows[0].nearby && rows[0].code == code,
          "the son's list has it, on his network");
    browsing.join(dad_id, son_id, now);
    browsing.tick(now += 200'000);
    const auto joined = browsing.take_join();
    check(joined && joined->code == code, "joining from the list gives the host's code");

    // Someone on another network does not see it.
    away.refresh(now);
    away.tick(now += 200'000);
    check(away.status().searched && away.status().rows.empty(), "another network's list is empty");

    // While Dad plays, the listing is published again each minute, so it never lapses.
    const int before = service.hosts;
    dad.clock += 61'000;
    hosting.update_host(true, 2, "levels/park/bam", now += 1'100'000); // a player joined: republished anyway
    dad.clock += 61'000;
    hosting.update_host(true, 2, "levels/park/bam", now += 1'100'000); // nothing changed: the minute is up
    check(service.hosts >= before + 2, "the listing is kept fresh");

    // Stopping takes it down.
    hosting.stop();
    check(!service.listings.contains(dad_id) && service.unhosts >= 1, "stopping unlists the game");

    // Browsing before the connection is there: the search waits for it, then finds the game.
    hosting.host(code, 8, false, "Dad's park", false);
    hosting.update_host(true, 1, "levels/park/bam", now += 1'100'000);
    hosting.tick(now += 200'000);
    hosting.update_host(true, 1, "levels/park/bam", now += 1'100'000);
    son.connected = false;
    SteamLobbies early(make_direct_lobby_api(son.channel()));
    early.refresh(now += 6'000'000);
    early.tick(now += 200'000);
    check(early.status().searching && early.status().browser.find("lobby service") != std::string::npos,
          "the search waits, and says where it is looking");
    son.connected = true;
    early.tick(now += 200'000);
    check(!early.status().searching && early.status().rows.size() == 1, "once connected the search finds the game");

    std::printf("%s\n", failures ? "FAILED" : "Direct lobbies: home listing, browsing, joining and refresh passed.");
    return failures ? 1 : 0;
}
