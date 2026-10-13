// Direct multiplayer's identity and lobby client.
//
//   dingosdk_direct_lobby_tests [lobby url]
//
// Always: Ed25519 against RFC 8032's first test vector, and a key, signature and player ID
// against values Node's crypto produced for the same seed (the service checks signatures and
// derives IDs with WebCrypto). With a lobby url (a running `wrangler dev`): two players connect
// to the service, are greeted as the IDs their keys give, and trade signals both ways.
#include "Extension/Multiplayer/Direct/direct_identity.h"
#include "Extension/Multiplayer/Direct/direct_lobby.h"

#include <chrono>
#include <cstdio>
#include <string>
#include <thread>

using namespace dingosdk::multiplayer;
using namespace std::chrono_literals;

namespace {
int failures = 0;
void check(bool ok, const char *what) {
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", what);
    }
}
template <std::size_t N> std::array<std::uint8_t, N> hex(const char *text) { return from_hex<N>(text).value(); }

void identity() {
    // RFC 8032 section 7.1, test 1: the empty message.
    const auto rfc = player_key_from_seed(hex<32>("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"));
    check(to_hex(rfc.public_key) == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "RFC 8032 public key");
    check(to_hex(sign(rfc, "")) ==
              "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
          "RFC 8032 signature");

    // Seed 01 02 .. 20, as Node's crypto saw it: the key, a lobby signature, the player ID.
    std::array<std::uint8_t, 32> seed{};
    for (std::size_t i = 0; i < seed.size(); ++i) seed[i] = static_cast<std::uint8_t>(i + 1);
    const auto key = player_key_from_seed(seed);
    check(to_hex(key.public_key) == "79b5562e8fe654f94078b112e8a98ba7901f853ae695bed7e0e3910bad049664", "public key matches Node");
    const auto signature = sign(key, "reskate-lobby-v1:abc");
    check(to_hex(signature) ==
              "92fb1d7c42022c6e40150fb77515e5f473bb719c3ec1cab44fe16946558c68be2e634737357d45c5eb00a2779d1c9c9cc31301f69d3eebd6b83a3ce7868aa106",
          "signature matches Node");
    check(verify(key.public_key, "reskate-lobby-v1:abc", signature), "signature verifies");
    check(!verify(key.public_key, "reskate-lobby-v1:abd", signature), "a changed message does not verify");
    check(player_id_of(key.public_key) == 292733981780477555ull, "player ID matches the service's");
    check(is_direct_player_id(player_id_of(key.public_key)), "player ID is a direct one");

    const auto a = generate_player_key(), b = generate_player_key();
    check(a && b && a->seed != b->seed, "fresh keys differ");
    check(a && player_key_from_seed(a->seed).public_key == a->public_key, "a key comes back from its seed");
    check(!from_hex<32>("zz") && !from_hex<32>(std::string(64, 'g')), "bad hex is refused");
}

bool wait_for(auto &&done, std::chrono::milliseconds limit) {
    const auto until = std::chrono::steady_clock::now() + limit;
    while (std::chrono::steady_clock::now() < until) {
        if (done()) return true;
        std::this_thread::sleep_for(20ms);
    }
    return false;
}

void lobby(const std::string &url) {
    const auto alice_key = generate_player_key().value(), bob_key = generate_player_key().value();
    const auto alice_id = player_id_of(alice_key.public_key), bob_id = player_id_of(bob_key.public_key);
    auto alice = make_lobby_signaling(url, alice_key);
    auto bob = make_lobby_signaling(url, bob_key);
    const bool both = wait_for([&] { return alice->connected() && bob->connected(); }, 15s);
    check(both, "both players connect to the lobby service");
    if (!both) {
        std::fprintf(stderr, "  alice: %s\n  bob: %s\n", alice->problem().c_str(), bob->problem().c_str());
        return;
    }
    const std::array<std::uint8_t, 3> hello{1, 2, 255};
    check(alice->send(bob_id, hello), "alice sends");
    std::vector<SignalMessage> got;
    check(wait_for([&] { for (auto &m : bob->receive()) got.push_back(std::move(m)); return !got.empty(); }, 5s),
          "bob receives alice's signal");
    check(!got.empty() && got[0].from == alice_id && got[0].bytes == std::vector<std::uint8_t>(hello.begin(), hello.end()),
          "it is from alice, unchanged");
    got.clear();
    const std::array<std::uint8_t, 1> back{42};
    check(bob->send(alice_id, back), "bob sends");
    check(wait_for([&] { for (auto &m : alice->receive()) got.push_back(std::move(m)); return !got.empty(); }, 5s),
          "alice receives bob's signal");
    check(!got.empty() && got[0].from == bob_id && got[0].bytes == std::vector<std::uint8_t>{42}, "it is from bob, unchanged");

    // Closing one end leaves the other connected; destroying both stops their threads.
    bob.reset();
    std::this_thread::sleep_for(200ms);
    check(alice->connected(), "alice stays connected when bob leaves");
    check(!alice->needs_new_key(), "alice's key is hers");

    auto nowhere = make_lobby_signaling("ftp://nowhere", alice_key);
    check(wait_for([&] { return nowhere->problem().find("not an http") != std::string::npos; }, 2s) && !nowhere->connected(),
          "a bad address is reported");
}
} // namespace

int main(int argc, char **argv) {
    identity();
    if (argc > 1) lobby(argv[1]);
    else std::printf("(no lobby url: the live lobby checks are skipped)\n");
    std::printf("%s\n", failures ? "FAILED" : "passed");
    return failures ? 1 : 0;
}
