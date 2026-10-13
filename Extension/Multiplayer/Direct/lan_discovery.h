#pragma once
#include <cstdint>
#include <string>
#include <vector>

// Games on this player's own network, found on the network itself: a browser broadcasts a
// question on UDP port lan_discovery_port, and a host there answers with its listing (the same
// rs_* fields a public listing has, join code included). Nothing goes through the internet, so a
// game can never be offered to strangers who merely share an address (carrier-grade NAT, a
// campus, a VPN). Questions and answers are taken only from private addresses (10/8, 172.16/12,
// 192.168/16, 169.254/16, and 127/8 for tests), and a host answers at most a few a second.
//
// Everything runs from the game's own polling: nothing blocks, and there are no threads.
namespace dingosdk::multiplayer {
inline constexpr std::uint16_t lan_discovery_port = 47777;

// An address (host byte order) a LAN question or answer may come from.
bool private_ipv4(std::uint32_t address);

class LanDiscovery {
  public:
    struct Found {
        std::string row; // the host's listing, a JSON object
        std::uint32_t from{};
    };
    explicit LanDiscovery(std::uint16_t port = lan_discovery_port);
    ~LanDiscovery();
    LanDiscovery(const LanDiscovery &) = delete;
    LanDiscovery &operator=(const LanDiscovery &) = delete;

    // Hosting: answer this network's questions with `row` (at most max_row bytes). Empty: stop
    // answering and close the port.
    void announce(std::string row);
    bool announcing() const { return !row_.empty(); }
    // Asks the network who is hosting (the broadcast address, or one address for tests). The
    // answers arrive over the next polls and replace the last search's.
    void search(std::uint32_t to = 0xffffffffu);
    // Answers questions, and collects answers. `now_ms`: a steady clock, for the answer rate.
    void poll(std::uint64_t now_ms);
    const std::vector<Found> &found() const { return found_; }
    // Why the port could not be opened (another program has it, say), for the log.
    const std::string &problem() const { return problem_; }

    static constexpr std::size_t max_row = 1200;

  private:
    std::uint16_t port_;
    std::intptr_t listener_ = -1, asker_ = -1;
    std::string row_, problem_;
    std::vector<Found> found_;
    std::uint64_t window_{};
    unsigned answered_{};
    bool network_{};
};
} // namespace dingosdk::multiplayer
